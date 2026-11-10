use std::{io, path::PathBuf};

use anyhow::{bail, Context, Result};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncWrite, AsyncWriteExt};

pub const PROTOCOL_VERSION: &str = "2.0";
pub const MAX_FRAME_BYTES: usize = 256 * 1024;
pub const DEFAULT_ENDPOINT: &str = "unison.sock";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct RpcRequest<P> {
    pub jsonrpc: String,
    pub id: u64,
    pub method: String,
    #[serde(default)]
    pub params: P,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct RpcResponse<R> {
    pub jsonrpc: String,
    pub id: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<R>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<RpcError>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct RpcError {
    pub code: i32,
    pub message: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct RpcEvent<P> {
    pub jsonrpc: String,
    pub method: String,
    pub params: P,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct EmptyParams {}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct SubscribeParams {
    pub since_sequence: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct NotificationEvent {
    pub sequence: u64,
    pub received_at_ms: i64,
    pub source: String,
    pub sender: String,
    pub body_preview: String,
    pub priority: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct MetricsSnapshot {
    pub p99_triage_latency_ms: f64,
    pub suppressed_total: u64,
    pub resident_memory_bytes: u64,
    pub queue_depth: u64,
    pub rules_version: u64,
}

pub type Endpoint = PathBuf;

pub fn default_endpoint() -> Endpoint {
    #[cfg(unix)]
    {
        std::env::var_os("XDG_RUNTIME_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir)
            .join("unison")
            .join(DEFAULT_ENDPOINT)
    }
    #[cfg(windows)]
    {
        PathBuf::from(r"\\.\pipe\unison")
    }
}

pub async fn read_frame<R, T>(reader: &mut R) -> Result<Option<T>>
where
    R: AsyncBufRead + Unpin,
    T: DeserializeOwned,
{
    let mut line = Vec::new();
    let bytes = reader.read_until(b'\n', &mut line).await?;
    if bytes == 0 {
        return Ok(None);
    }
    if line.len() > MAX_FRAME_BYTES {
        bail!("IPC frame exceeds {} bytes", MAX_FRAME_BYTES);
    }
    let frame = serde_json::from_slice(line.trim_ascii()).context("invalid JSON-RPC frame")?;
    Ok(Some(frame))
}

pub async fn write_frame<W, T>(writer: &mut W, value: &T) -> Result<()>
where
    W: AsyncWrite + Unpin,
    T: Serialize,
{
    let mut bytes = serde_json::to_vec(value)?;
    if bytes.len() > MAX_FRAME_BYTES {
        bail!("IPC frame exceeds {} bytes", MAX_FRAME_BYTES);
    }
    bytes.push(b'\n');
    writer.write_all(&bytes).await?;
    writer.flush().await?;
    Ok(())
}

pub fn validate_request<P>(request: &RpcRequest<P>) -> io::Result<()> {
    if request.jsonrpc != PROTOCOL_VERSION {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unsupported JSON-RPC version",
        ));
    }
    if request.method.is_empty() || request.method.len() > 128 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid method name",
        ));
    }
    Ok(())
}

#[cfg(unix)]
pub mod transport {
    use super::Endpoint;
    use anyhow::{Context, Result};
    use std::{
        fs,
        os::unix::fs::{FileTypeExt, PermissionsExt},
    };
    use tokio::net::{UnixListener, UnixStream};

    pub type ClientStream = UnixStream;
    pub type ServerListener = UnixListener;

    pub async fn bind(endpoint: &Endpoint) -> Result<ServerListener> {
        if let Some(parent) = endpoint.parent() {
            fs::create_dir_all(parent)?;
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
        }
        if endpoint.exists() {
            fs::remove_file(endpoint).context("removing stale Unison socket")?;
        }
        let listener = UnixListener::bind(endpoint)?;
        fs::set_permissions(endpoint, fs::Permissions::from_mode(0o600))?;
        Ok(listener)
    }

    pub async fn connect(endpoint: &Endpoint) -> Result<ClientStream> {
        authorize(endpoint)?;
        Ok(UnixStream::connect(endpoint).await?)
    }

    pub fn authorize(endpoint: &Endpoint) -> Result<()> {
        let metadata =
            fs::symlink_metadata(endpoint).context("reading Unison socket permissions")?;
        if !metadata.file_type().is_socket() {
            anyhow::bail!("IPC endpoint is not a Unix domain socket");
        }
        let mode = metadata.permissions().mode();
        if mode & 0o077 != 0 {
            anyhow::bail!("refusing IPC socket accessible by group or other users");
        }
        Ok(())
    }
}

#[cfg(windows)]
pub mod transport {
    use super::Endpoint;
    use anyhow::Result;
    use tokio::net::windows::named_pipe::{ClientOptions, NamedPipeServer, ServerOptions};

    pub type ClientStream = tokio::net::windows::named_pipe::NamedPipeClient;
    pub type ServerListener = NamedPipeServer;

    pub async fn bind(endpoint: &Endpoint) -> Result<ServerListener> {
        Ok(ServerOptions::new()
            .first_pipe_instance(true)
            .create(endpoint)?)
    }

    pub async fn connect(endpoint: &Endpoint) -> Result<ClientStream> {
        Ok(ClientOptions::new().open(endpoint)?)
    }

    pub fn authorize(_endpoint: &Endpoint) -> Result<()> {
        // The daemon must create this pipe with a security descriptor granting
        // access only to the current user SID. Named-pipe ACL configuration is
        // applied at creation; there is no portable metadata mode to inspect.
        Ok(())
    }
}
