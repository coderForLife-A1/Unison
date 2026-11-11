//! Platform notification listeners which normalize into a daemon-owned stream.
//!
//! The platform callbacks are deliberately thin. They only enqueue bounded,
//! normalized values into a Tokio channel; core triage runs elsewhere.

use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::Result;
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;

pub const MAX_TEXT_BYTES: usize = 8 * 1024;
pub const MAX_ACTIONS: usize = 16;
pub const CHANNEL_CAPACITY: usize = 256;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct ActionButton {
    pub id: String,
    pub label: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct NotificationEvent {
    pub platform: String,
    pub app_id: String,
    pub title: String,
    pub body: String,
    pub actions: Vec<ActionButton>,
    pub received_at_ms: i64,
}

impl NotificationEvent {
    pub fn new(
        platform: impl Into<String>,
        app_id: impl Into<String>,
        title: impl Into<String>,
        body: impl Into<String>,
        actions: Vec<ActionButton>,
    ) -> Self {
        Self {
            platform: platform.into(),
            app_id: truncate_text(&app_id.into()),
            title: truncate_text(&title.into()),
            body: truncate_text(&body.into()),
            actions: actions.into_iter().take(MAX_ACTIONS).collect(),
            received_at_ms: unix_time_ms(),
        }
    }
}

pub fn channel() -> (
    mpsc::Sender<NotificationEvent>,
    mpsc::Receiver<NotificationEvent>,
) {
    mpsc::channel(CHANNEL_CAPACITY)
}

pub fn try_deliver(
    sender: &mpsc::Sender<NotificationEvent>,
    event: NotificationEvent,
) -> Result<()> {
    sender.try_send(event).map_err(|error| {
        anyhow::anyhow!("notification bridge channel unavailable or full: {error}")
    })
}

fn truncate_text(value: &str) -> String {
    if value.len() <= MAX_TEXT_BYTES {
        return value.to_owned();
    }
    let mut end = MAX_TEXT_BYTES;
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].to_owned()
}

fn unix_time_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(i64::MAX as u128) as i64
}

pub struct NotificationBridge;

impl NotificationBridge {
    pub async fn run(sender: mpsc::Sender<NotificationEvent>) -> Result<()> {
        #[cfg(unix)]
        {
            unix::run(sender).await
        }
        #[cfg(windows)]
        {
            windows::run(sender).await
        }
        #[cfg(not(any(unix, windows)))]
        {
            let _ = sender;
            anyhow::bail!("notification bridge is unsupported on this platform")
        }
    }
}

#[cfg(unix)]
mod unix {
    use super::{try_deliver, ActionButton, NotificationEvent};
    use anyhow::{Context, Result};
    use std::collections::HashMap;
    use tokio::sync::mpsc;
    use zbus::{interface, Connection, Result as ZbusResult};

    #[derive(Clone)]
    struct NotificationServer {
        sender: mpsc::Sender<NotificationEvent>,
    }

    #[interface(name = "org.freedesktop.Notifications")]
    impl NotificationServer {
        async fn notify(
            &self,
            app_name: &str,
            _replaces_id: u32,
            _app_icon: &str,
            summary: &str,
            body: &str,
            actions: Vec<String>,
            _hints: HashMap<String, zbus::zvariant::OwnedValue>,
            _expire_timeout: i32,
        ) -> ZbusResult<u32> {
            let action_buttons = actions
                .chunks_exact(2)
                .take(super::MAX_ACTIONS)
                .map(|pair| ActionButton {
                    id: pair[0].clone(),
                    label: pair[1].clone(),
                })
                .collect();
            let event =
                NotificationEvent::new("linux-dbus", app_name, summary, body, action_buttons);
            try_deliver(&self.sender, event)
                .map_err(|error| zbus::fdo::Error::Failed(error.to_string()))?;
            Ok(0)
        }

        async fn get_capabilities(&self) -> Vec<&str> {
            vec!["body", "actions"]
        }

        async fn get_server_information(&self) -> (&str, &str, &str, &str) {
            ("Unison", "Unison", "1.0", "1.2")
        }
    }

    pub async fn run(sender: mpsc::Sender<NotificationEvent>) -> Result<()> {
        let connection = Connection::session()
            .await
            .context("connecting to the Linux session DBus")?;
        connection
            .request_name("org.freedesktop.Notifications")
            .await
            .context("claiming org.freedesktop.Notifications")?;
        connection
            .object_server()
            .at(
                "/org/freedesktop/Notifications",
                NotificationServer { sender },
            )
            .await
            .context("registering notification DBus interface")?;
        std::future::pending::<()>().await;
        Ok(())
    }
}

#[cfg(windows)]
mod windows {
    use super::{try_deliver, ActionButton, NotificationEvent};
    use anyhow::{bail, Context, Result};
    use std::sync::Arc;
    use tokio::sync::mpsc;
    use windows::{
        core::TypedEventHandler,
        UI::Notifications::Management::{
            UserNotificationChangedEventArgs, UserNotificationListener,
            UserNotificationListenerAccessStatus,
        },
        UI::Notifications::{KnownNotificationBindings, NotificationKinds},
    };

    pub async fn run(sender: mpsc::Sender<NotificationEvent>) -> Result<()> {
        let listener = UserNotificationListener::Current()
            .context("opening Windows UserNotificationListener")?;
        let access = listener
            .RequestAccessAsync()
            .context("requesting Windows notification listener access")?
            .await
            .context("awaiting Windows notification listener access")?;
        if access != UserNotificationListenerAccessStatus::Allowed {
            bail!("Windows notification listener permission was not granted: {access:?}");
        }

        let sender = Arc::new(sender);
        let callback_sender = Arc::clone(&sender);
        let handler = TypedEventHandler::new(
            move |listener: &Option<UserNotificationListener>,
                  _args: &Option<UserNotificationChangedEventArgs>| {
                let Some(listener) = listener.clone() else {
                    return Ok(());
                };
                let sender = Arc::clone(&callback_sender);
                tokio::spawn(async move {
                    if let Err(error) = collect_notifications(listener, sender).await {
                        eprintln!("Windows notification collection failed: {error:#}");
                    }
                });
                Ok(())
            },
        );
        listener
            .NotificationChanged(&handler)
            .context("subscribing to Windows notification changes")?;
        std::future::pending::<()>().await;
        Ok(())
    }

    async fn collect_notifications(
        listener: UserNotificationListener,
        sender: Arc<mpsc::Sender<NotificationEvent>>,
    ) -> Result<()> {
        let notifications = listener
            .GetNotificationsAsync(NotificationKinds::Toast)
            .context("requesting Windows toast notifications")?
            .await
            .context("awaiting Windows toast notifications")?;
        for index in 0..notifications.Size()? {
            let notification = notifications.GetAt(index)?;
            let app_id = notification.AppInfo()?.AppUserModelId()?.to_string_lossy();
            let binding = notification
                .Notification()?
                .Visual()?
                .GetBinding(&KnownNotificationBindings::ToastGeneric()?)?;
            let texts = binding.GetTextElements()?;
            let mut values = Vec::new();
            for text_index in 0..texts.Size()? {
                values.push(texts.GetAt(text_index)?.Text()?.to_string_lossy());
            }
            let title = values.first().cloned().unwrap_or_default();
            let body = values
                .iter()
                .skip(1)
                .cloned()
                .collect::<Vec<_>>()
                .join("\n");
            // UserNotificationListener exposes normalized visual text but not
            // toast action XML. Keep actions empty rather than guessing from
            // display text; action extraction requires a separate app-owned
            // toast source or a future Windows API that exposes it.
            let event = NotificationEvent::new("windows-winrt", app_id, title, body, Vec::new());
            try_deliver(&sender, event)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_and_bounds_platform_event() {
        let event = NotificationEvent::new(
            "test",
            "app",
            "title",
            "x".repeat(MAX_TEXT_BYTES + 1),
            (0..MAX_ACTIONS + 2)
                .map(|index| ActionButton {
                    id: index.to_string(),
                    label: "button".to_owned(),
                })
                .collect(),
        );
        assert_eq!(event.body.len(), MAX_TEXT_BYTES);
        assert_eq!(event.actions.len(), MAX_ACTIONS);
    }
}
