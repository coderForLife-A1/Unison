"""IMAP email ingestion adapter.

Uses Python's stdlib ``imaplib`` and ``email`` modules to poll an IMAP
mailbox for UNSEEN messages and convert them into
``IngestedNotification`` objects.  All blocking IMAP I/O is offloaded to
a thread via ``asyncio.to_thread`` so the event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import uuid
from datetime import datetime
from email.header import decode_header
from email.message import Message
from typing import Optional

from src.config import settings
from src.ingestion.base import BaseAdapter
from src.models.schemas import IngestedNotification

logger = logging.getLogger(__name__)


class EmailAdapter(BaseAdapter):
    """Polls an IMAP mailbox for unread messages.

    Configuration is sourced from the global ``settings`` singleton:
    ``imap_host``, ``imap_port``, ``imap_user``, ``imap_password``,
    ``imap_use_ssl``, and ``imap_folder``.

    If IMAP is not configured (host or user is empty), ``fetch`` returns
    an empty list silently.
    """

    @property
    def name(self) -> str:
        """Return the adapter display name."""
        return "EmailAdapter"

    # ── Public API ──────────────────────────────────────────────────────

    async def fetch(self) -> list[IngestedNotification]:
        """Fetch unseen emails from the configured IMAP folder.

        Returns:
            A list of ``IngestedNotification`` objects (``source="Email"``).
            Returns an empty list when IMAP is not configured or when a
            connection error occurs.
        """
        if not self._is_configured():
            logger.debug("IMAP not configured — skipping EmailAdapter.")
            return []

        try:
            return await asyncio.to_thread(self._fetch_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning("EmailAdapter connection error: %s", exc)
            return []

    # ── Synchronous IMAP logic (runs in a worker thread) ────────────────

    def _fetch_sync(self) -> list[IngestedNotification]:
        """Blocking IMAP fetch — called via ``asyncio.to_thread``.

        Returns:
            Parsed notifications from unseen messages.
        """
        conn = self._connect()
        if conn is None:
            return []

        try:
            conn.select(settings.imap_folder)
            _status, msg_ids = conn.search(None, "UNSEEN")

            if not msg_ids or not msg_ids[0]:
                return []

            notifications: list[IngestedNotification] = []
            for num in msg_ids[0].split():
                _status, msg_data = conn.fetch(num, "(RFC822)")
                if not msg_data or msg_data[0] is None:
                    continue

                raw_email = msg_data[0]
                # msg_data[0] is a tuple of (flags, raw_bytes)
                if isinstance(raw_email, tuple):
                    raw_bytes = raw_email[1]
                else:
                    raw_bytes = raw_email  # type: ignore[assignment]

                parsed = self._parse_email(raw_bytes)
                if parsed is not None:
                    notifications.append(parsed)

            return notifications
        finally:
            try:
                conn.close()
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_configured() -> bool:
        """Check whether IMAP credentials are present in settings."""
        return bool(settings.imap_host and settings.imap_user)

    @staticmethod
    def _connect() -> Optional[imaplib.IMAP4 | imaplib.IMAP4_SSL]:
        """Open a connection to the IMAP server.

        Returns:
            An authenticated IMAP connection, or ``None`` on failure.
        """
        try:
            if settings.imap_use_ssl:
                conn = imaplib.IMAP4_SSL(
                    settings.imap_host, settings.imap_port
                )
            else:
                conn = imaplib.IMAP4(settings.imap_host, settings.imap_port)

            conn.login(settings.imap_user, settings.imap_password)
            return conn
        except (imaplib.IMAP4.error, OSError) as exc:
            logger.warning("Failed to connect to IMAP server: %s", exc)
            return None

    @staticmethod
    def _decode_header_value(value: str | None) -> str:
        """Decode an RFC-2047 encoded header into a plain string."""
        if not value:
            return ""
        parts = decode_header(value)
        decoded_parts: list[str] = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded_parts.append(
                    part.decode(charset or "utf-8", errors="replace")
                )
            else:
                decoded_parts.append(part)
        return " ".join(decoded_parts)

    @classmethod
    def _extract_body(cls, msg: Message) -> str:
        """Extract the plain-text body from an email message.

        Falls back to an empty string if no text/plain part exists.
        """
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition", ""))
                if (
                    content_type == "text/plain"
                    and "attachment" not in disposition
                ):
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
            return ""
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
            return ""

    @classmethod
    def _parse_email(cls, raw_bytes: bytes) -> IngestedNotification | None:
        """Parse raw RFC-822 bytes into an ``IngestedNotification``.

        Args:
            raw_bytes: The raw email message bytes.

        Returns:
            A validated notification, or ``None`` on parse failure.
        """
        try:
            msg = email.message_from_bytes(raw_bytes)
            sender = cls._decode_header_value(msg.get("From", ""))
            subject = cls._decode_header_value(msg.get("Subject", ""))
            body = cls._extract_body(msg).strip()

            # Combine subject and body for the notification content
            content_parts = []
            if subject:
                content_parts.append(f"Subject: {subject}")
            if body:
                content_parts.append(body)
            content = "\n".join(content_parts) if content_parts else "(empty)"

            # Try to parse the Date header
            date_str = msg.get("Date", "")
            try:
                ts = email.utils.parsedate_to_datetime(date_str)
            except Exception:  # noqa: BLE001
                ts = datetime.now()

            return IngestedNotification(
                id=uuid.uuid4().hex,
                source="Email",
                sender=sender,
                content=content,
                timestamp=ts,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to parse email message: %s", exc)
            return None
