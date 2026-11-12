use std::sync::Arc;

use anyhow::Result;
use serde::{Deserialize, Serialize};
use tokio::{
    io::{BufReader, BufWriter},
    sync::{broadcast, mpsc},
};
use unison_ipc::{
    default_endpoint, transport, validate_request, MetricsSnapshot, NotificationEvent, RpcError,
    RpcEvent, RpcRequest, RpcResponse, SubscribeParams, PROTOCOL_VERSION,
};
use unison_notification_bridge::NotificationBridge;

#[derive(Clone, Debug, Deserialize, Serialize, Default)]
struct RequestParams {
    #[serde(flatten)]
    subscribe: Option<SubscribeParams>,
    notification: Option<BenchmarkNotification>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct BenchmarkNotification {
    id: String,
    source: String,
    sender: String,
    body: String,
    received_at_ms: i64,
}

#[tokio::main]
async fn main() -> Result<()> {
    let endpoint = default_endpoint();
    let (events, _) = broadcast::channel::<NotificationEvent>(256);
    let events = Arc::new(events);
    let (bridge_sender, mut bridge_receiver) = mpsc::channel(256);
    tokio::spawn(async move {
        if let Err(error) = NotificationBridge::run(bridge_sender).await {
            eprintln!("notification bridge stopped: {error:#}");
        }
    });
    let event_broadcast = Arc::clone(&events);
    tokio::spawn(async move {
        let mut sequence = 0;
        while let Some(event) = bridge_receiver.recv().await {
            sequence += 1;
            let _ = event_broadcast.send(NotificationEvent {
                sequence,
                received_at_ms: event.received_at_ms,
                source: event.app_id,
                sender: event.title,
                body_preview: event.body,
                priority: "Unknown".to_owned(),
            });
        }
    });

    #[cfg(unix)]
    {
        run_unix(endpoint, events).await
    }
    #[cfg(windows)]
    {
        run_windows(endpoint, events).await
    }
}

#[cfg(unix)]
async fn run_unix(
    endpoint: std::path::PathBuf,
    events: Arc<broadcast::Sender<NotificationEvent>>,
) -> Result<()> {
    let listener = transport::bind(&endpoint).await?;
    loop {
        let (stream, _) = listener.accept().await?;
        let events = Arc::clone(&events);
        tokio::spawn(async move {
            if let Err(error) = serve_connection(stream, events).await {
                eprintln!("unisond IPC connection closed: {error:#}");
            }
        });
    }
}

#[cfg(windows)]
async fn run_windows(
    endpoint: std::path::PathBuf,
    events: Arc<broadcast::Sender<NotificationEvent>>,
) -> Result<()> {
    let mut listener = transport::bind(&endpoint).await?;
    loop {
        listener.connect().await?;
        let stream = listener;
        listener = transport::bind(&endpoint).await?;
        let events = Arc::clone(&events);
        tokio::spawn(async move {
            if let Err(error) = serve_connection(stream, events).await {
                eprintln!("unisond IPC connection closed: {error:#}");
            }
        });
    }
}

async fn serve_connection<S>(
    stream: S,
    events: Arc<broadcast::Sender<NotificationEvent>>,
) -> Result<()>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let (reader, writer) = tokio::io::split(stream);
    let mut reader = BufReader::new(reader);
    let mut writer = BufWriter::new(writer);
    while let Some(request) =
        unison_ipc::read_frame::<_, RpcRequest<RequestParams>>(&mut reader).await?
    {
        if let Err(error) = validate_request(&request) {
            let response = RpcResponse::<()>::error(request.id, -32600, error.to_string());
            unison_ipc::write_frame(&mut writer, &response).await?;
            continue;
        }
        match request.method.as_str() {
            "health" => {
                let result = MetricsSnapshot {
                    p99_triage_latency_ms: 0.0,
                    suppressed_total: 0,
                    resident_memory_bytes: 0,
                    queue_depth: 0,
                    rules_version: 0,
                };
                unison_ipc::write_frame(&mut writer, &RpcResponse::success(request.id, result))
                    .await?;
            }
            "subscribe" => {
                let since = request
                    .params
                    .subscribe
                    .map(|params| params.since_sequence)
                    .unwrap_or(0);
                unison_ipc::write_frame(
                    &mut writer,
                    &RpcResponse::success(
                        request.id,
                        SubscribeAck {
                            since_sequence: since,
                        },
                    ),
                )
                .await?;
                let mut receiver = events.subscribe();
                while let Ok(event) = receiver.recv().await {
                    let notification = RpcEvent {
                        jsonrpc: PROTOCOL_VERSION.to_owned(),
                        method: "notification.received".to_owned(),
                        params: event,
                    };
                    unison_ipc::write_frame(&mut writer, &notification).await?;
                }
                return Ok(());
            }
            "benchmark.ingest" => {
                if std::env::var_os("UNISON_BENCHMARK").as_deref() != Some(std::ffi::OsStr::new("1")) {
                    unison_ipc::write_frame(
                        &mut writer,
                        &RpcResponse::<()>::error(
                            request.id,
                            -32601,
                            "benchmark ingestion is disabled",
                        ),
                    )
                    .await?;
                    continue;
                }
                let Some(notification) = request.params.notification else {
                    unison_ipc::write_frame(
                        &mut writer,
                        &RpcResponse::<()>::error(
                            request.id,
                            -32602,
                            "benchmark.ingest requires notification params",
                        ),
                    )
                    .await?;
                    continue;
                };
                if notification.id.len() > 128
                    || notification.source.len() > 64
                    || notification.sender.len() > 512
                    || notification.body.len() > 256 * 1024
                {
                    unison_ipc::write_frame(
                        &mut writer,
                        &RpcResponse::<()>::error(request.id, -32602, "notification is too large"),
                    )
                    .await?;
                    continue;
                }
                let event = NotificationEvent {
                    sequence: 0,
                    received_at_ms: notification.received_at_ms,
                    source: notification.source,
                    sender: notification.sender,
                    body_preview: notification.body,
                    priority: "Unknown".to_owned(),
                };
                let _ = events.send(event);
                unison_ipc::write_frame(
                    &mut writer,
                    &RpcResponse::success(request.id, true),
                )
                .await?;
            }
            "get_snapshot" => {
                unison_ipc::write_frame(
                    &mut writer,
                    &RpcResponse::success(request.id, Snapshot::default()),
                )
                .await?;
            }
            _ => {
                unison_ipc::write_frame(
                    &mut writer,
                    &RpcResponse::<()>::error(request.id, -32601, "method not found"),
                )
                .await?;
            }
        }
    }
    Ok(())
}

#[derive(Serialize)]
struct SubscribeAck {
    since_sequence: u64,
}

#[derive(Default, Serialize)]
struct Snapshot {
    notifications: Vec<NotificationEvent>,
}

impl<R> RpcResponse<R> {
    fn success(id: u64, result: R) -> Self {
        Self {
            jsonrpc: PROTOCOL_VERSION.to_owned(),
            id,
            result: Some(result),
            error: None,
        }
    }

    fn error(id: u64, code: i32, message: impl Into<String>) -> Self {
        Self {
            jsonrpc: PROTOCOL_VERSION.to_owned(),
            id,
            result: None,
            error: Some(RpcError {
                code,
                message: message.into(),
            }),
        }
    }
}
