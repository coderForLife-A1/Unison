"""Mock ingestion adapter for demo and testing.

Generates a realistic set of 8-12 sample notifications spanning SMS,
WhatsApp, Email, and System sources.  Each call to ``fetch`` shuffles
and slightly varies the samples so repeated runs feel natural.

.. warning::
   This adapter is **only** for testing and demo purposes.
   It must never be used in production ingestion paths.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from src.ingestion.base import BaseAdapter
from src.models.schemas import IngestedNotification


# ── Realistic sample pool ───────────────────────────────────────────────

_SAMPLE_POOL: list[dict] = [
    # OTP / Verification
    {
        "source": "SMS",
        "sender": "+1-800-555-0199",
        "content": "Your verification code is 483920. It expires in 10 minutes. Do not share this code.",
    },
    {
        "source": "SMS",
        "sender": "BankOfAmerica",
        "content": "Your one-time passcode for login is 715263. If you didn't request this, call us immediately.",
    },
    # Personal / Group chat
    {
        "source": "WhatsApp",
        "sender": "Family Group",
        "content": "Mom: Are we still meeting Sunday at 1 PM for lunch?",
    },
    {
        "source": "WhatsApp",
        "sender": "Family Group",
        "content": "Dad: Yes! I'll bring the cake. Can someone pick up flowers?",
    },
    {
        "source": "WhatsApp",
        "sender": "Priya Sharma",
        "content": "Hey, did you finish the report? Boss was asking about it in the morning standup.",
    },
    {
        "source": "WhatsApp",
        "sender": "College Friends",
        "content": "Rahul: Anyone up for a movie this Saturday? The new Nolan film is out!",
    },
    # Work emails
    {
        "source": "Email",
        "sender": "jira@company.atlassian.net",
        "content": "Subject: [PROJ-1042] Bug fix merged — Sprint velocity updated. "
        "Your pull request has been merged into main. CI/CD pipeline triggered.",
    },
    {
        "source": "Email",
        "sender": "manager@company.com",
        "content": "Subject: Q3 Planning — Please review the attached roadmap doc before Thursday's meeting and add your estimates.",
    },
    {
        "source": "Email",
        "sender": "hr@company.com",
        "content": "Subject: Updated PTO Policy — The revised leave policy is effective from Nov 1. Please review the attached PDF.",
    },
    # Promotional / Noise
    {
        "source": "Email",
        "sender": "deals@amazon.in",
        "content": "Subject: Great Indian Festival — Up to 70% off on electronics! Shop now before stock runs out.",
    },
    {
        "source": "SMS",
        "sender": "AD-SWIGGY",
        "content": "Craving biryani? Order now and get 40% OFF with code YUMMY40. Valid till midnight!",
    },
    # System notifications
    {
        "source": "System",
        "sender": "Windows Update",
        "content": "Feature update to Windows 11, version 24H2 is ready to install. Restart required.",
    },
    {
        "source": "System",
        "sender": "Dropbox",
        "content": "Sync complete — 14 files updated across 3 shared folders.",
    },
    {
        "source": "System",
        "sender": "Slack",
        "content": "You have 5 unread messages in #engineering and 2 direct messages from @alice.",
    },
]


class MockAdapter(BaseAdapter):
    """Generates realistic sample notifications for demo/testing.

    Each call to ``fetch`` randomly selects 8-12 entries from a curated
    pool of realistic messages, assigns fresh IDs and slightly jittered
    timestamps, and returns them as validated ``IngestedNotification``
    objects.

    .. warning::
       This adapter is only for testing/demo — never use in production.
    """

    @property
    def name(self) -> str:
        """Return the adapter display name."""
        return "MockAdapter"

    async def fetch(self) -> list[IngestedNotification]:
        """Return 8-12 varied sample notifications.

        Returns:
            A list of ``IngestedNotification`` with randomised
            timestamps and unique IDs.
        """
        count = random.randint(8, 12)
        samples = random.sample(_SAMPLE_POOL, k=min(count, len(_SAMPLE_POOL)))

        now = datetime.now()
        notifications: list[IngestedNotification] = []

        for idx, sample in enumerate(samples):
            # Jitter timestamps so each message looks like it arrived at a
            # slightly different time over the past hour.
            jitter = timedelta(minutes=random.randint(0, 60))
            ts = now - jitter

            notifications.append(
                IngestedNotification(
                    id=uuid.uuid4().hex,
                    source=sample["source"],  # type: ignore[arg-type]
                    sender=sample["sender"],
                    content=sample["content"],
                    timestamp=ts,
                )
            )

        return notifications
