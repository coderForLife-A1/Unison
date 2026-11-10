use anyhow::{Context, Result};
use tokio::io::{BufReader, BufWriter};
use unison_ipc::{
    default_endpoint, transport, EmptyParams, MetricsSnapshot, RpcRequest, RpcResponse,
    PROTOCOL_VERSION,
};

#[tokio::main]
async fn main() -> Result<()> {
    let endpoint = default_endpoint();
    let stream = tokio::time::timeout(
        std::time::Duration::from_secs(2),
        transport::connect(&endpoint),
    )
    .await
    .context("timed out connecting to unisond")??;
    let (reader, writer) = tokio::io::split(stream);
    let mut reader = BufReader::new(reader);
    let mut writer = BufWriter::new(writer);
    let request = RpcRequest {
        jsonrpc: PROTOCOL_VERSION.to_owned(),
        id: 1,
        method: "health".to_owned(),
        params: EmptyParams::default(),
    };
    unison_ipc::write_frame(&mut writer, &request).await?;
    let response: RpcResponse<MetricsSnapshot> = unison_ipc::read_frame(&mut reader)
        .await?
        .context("unisond closed the connection")?;
    match (response.result, response.error) {
        (Some(metrics), None) => {
            println!(
                "unisond ready: queue={} suppressed={} p99={:.1}ms",
                metrics.queue_depth, metrics.suppressed_total, metrics.p99_triage_latency_ms
            );
            Ok(())
        }
        (_, Some(error)) => anyhow::bail!("unisond error {}: {}", error.code, error.message),
        _ => anyhow::bail!("invalid JSON-RPC response"),
    }
}
