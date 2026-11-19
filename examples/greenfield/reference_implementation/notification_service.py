"""
Reference implementation for AcmeBank Payment Notification Service (P017).
"""

import hashlib
import hmac
from dataclasses import dataclass


@dataclass
class PaymentEvent:
    """Incoming payment completion event."""

    event_id: str
    payment_id: str
    status: str
    amount_cents: int
    currency: str
    recipient_label: str
    timestamp: str


@dataclass
class NotificationPayload:
    """Push notification payload with no PII exposure."""

    payment_id: str
    status: str
    masked_amount: str
    recipient_label: str


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 webhook signature (NFR-003)."""
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def format_notification(event: PaymentEvent) -> NotificationPayload:
    """Format push notification payload without exposing sensitive PII (FR-002)."""
    dollars = event.amount_cents / 100.0
    masked = f"{event.currency} {dollars:,.2f}"
    safe_recipient = event.recipient_label.split("@")[0][:24]
    return NotificationPayload(
        payment_id=event.payment_id,
        status=event.status,
        masked_amount=masked,
        recipient_label=safe_recipient,
    )
