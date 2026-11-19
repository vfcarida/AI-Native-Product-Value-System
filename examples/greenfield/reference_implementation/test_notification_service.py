"""
Reference unit tests for AcmeBank Payment Notification Service (P017).
"""

import hashlib
import hmac

from notification_service import (
    PaymentEvent,
    format_notification,
    verify_webhook_signature,
)


def test_verify_webhook_signature() -> None:
    """Test HMAC-SHA256 signature verification."""
    body = b'{"event_id":"evt_1","status":"completed"}'
    secret = "synthetic-webhook-secret"
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(body, sig, secret) is True
    assert verify_webhook_signature(body, "bad-sig", secret) is False


def test_format_notification_strips_email_pii() -> None:
    """Test notification formatting strips email domains and formats currency."""
    event = PaymentEvent(
        event_id="evt_abc123",
        payment_id="pay_xyz789",
        status="completed",
        amount_cents=10000,
        currency="USD",
        recipient_label="merchant@example.com",
        timestamp="2026-11-18T10:45:00Z",
    )
    payload = format_notification(event)
    assert payload.masked_amount == "USD 100.00"
    assert "@" not in payload.recipient_label
