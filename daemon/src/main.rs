use std::sync::Arc;

use anyhow::Result;
use serde::{Deserialize, Serialize};
use tokio::{
    io::{BufReader, BufWriter},
    sync::broadcast,
};
use unison_ipc::{
    default_endpoint, transport, validate_request, MetricsSnapshot, NotificationEvent, RpcError,
    RpcEvent, RpcRequest, RpcResponse, SubscribeParams, PROTOCOL_VERSION,
};

#[derive(Clone, Debug, Deserialize, Serialize, Default)]
struct RequestParams {
    #[serde(flatten)]
    subscribe: Option<SubscribeParams>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let endpoint = default_endpoint();
    let (events, _) = broadcast::channel::<NotificationEvent>(256);
    let events = Arc::new(events);

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
