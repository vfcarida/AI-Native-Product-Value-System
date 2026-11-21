"""
Unit tests for PIIRedactor (emails, phones, CPFs, cards, addresses, names) (EVO-022).
"""

from __future__ import annotations

import pytest

from product_value_system.discovery import PIIRedactor, TranscriptProcessor


@pytest.mark.unit
def test_redact_email() -> None:
    """Email addresses are redacted and counted."""
    redactor = PIIRedactor()
    text = "Contact me at john.doe@example.com for more info"
    redacted, counts = redactor.redact(text)

    assert "[EMAIL]" in redacted
    assert "john.doe@example.com" not in redacted
    assert counts["emails"] == 1


@pytest.mark.unit
def test_redact_brazilian_phone() -> None:
    """Brazilian phone numbers (+55 and local DDD formats) are redacted."""
    redactor = PIIRedactor()
    text = "Call me at +55 (11) 98765-4321 or (21) 99876-5432"
    redacted, counts = redactor.redact(text)

    assert "[PHONE]" in redacted
    assert "98765-4321" not in redacted
    assert "99876-5432" not in redacted
    assert counts["phones"] == 2


@pytest.mark.unit
def test_redact_us_phone() -> None:
    """US and international phone numbers are redacted."""
    redactor = PIIRedactor()
    text = "Reach support at +1 (415) 555-0199 or 415-555-0142"
    redacted, counts = redactor.redact(text)

    assert "555-0199" not in redacted
    assert "555-0142" not in redacted
    assert counts["phones"] == 2


@pytest.mark.unit
def test_redact_cpf_formatted_and_labeled() -> None:
    """Brazilian CPF numbers (formatted and labeled 11-digit) are redacted."""
    redactor = PIIRedactor()
    text = "My CPF is 123.456.789-01 and secondary CPF: 98765432100."
    redacted, counts = redactor.redact(text)

    assert "123.456.789-01" not in redacted
    assert "98765432100" not in redacted
    assert redacted.count("[CPF]") == 2
    assert counts["cpfs"] == 2


@pytest.mark.unit
def test_redact_credit_card() -> None:
    """16-digit payment card numbers are redacted."""
    redactor = PIIRedactor()
    text = "I tried paying with card 4111-2222-3333-4444 yesterday."
    redacted, counts = redactor.redact(text)

    assert "[CARD]" in redacted
    assert "4111-2222-3333-4444" not in redacted
    assert counts["cards"] == 1


@pytest.mark.unit
def test_redact_english_and_portuguese_addresses() -> None:
    """Both English and Brazilian street addresses are redacted."""
    redactor = PIIRedactor()
    text = "Shipped to 450 Market Street and billed at Av. Paulista, 1000."
    redacted, counts = redactor.redact(text)

    assert "450 Market Street" not in redacted
    assert "Av. Paulista, 1000" not in redacted
    assert counts["addresses"] == 2


@pytest.mark.unit
def test_redact_names_without_corrupting_common_words() -> None:
    """Word-boundary name matching redacts real names without corrupting prose words."""
    redactor = PIIRedactor()
    text = "Ana Silva reviews customer analytics and analyzes transfer speed."
    redacted, counts = redactor.redact(text)

    assert "Ana" not in redacted
    assert "Silva" not in redacted
    assert "[NAME]" in redacted
    assert "analytics" in redacted
    assert "analyzes" in redacted
    assert counts["names"] == 1


@pytest.mark.unit
def test_redact_self_introduction_and_extra_names() -> None:
    """Self-introductions and caller-supplied extra_names are redacted."""
    redactor = PIIRedactor(extra_names=["Zuleika"])
    text = "Hi, my name is Henrique Costa and Zuleika also uses PIX daily."
    redacted, counts = redactor.redact(text)

    assert "Henrique" not in redacted
    assert "Zuleika" not in redacted
    assert counts["names"] >= 2


@pytest.mark.unit
def test_redact_multiple_pii_types() -> None:
    """Multiple PII types in a single sentence are all redacted cleanly."""
    redactor = PIIRedactor()
    text = "João Silva (joao@email.com, +55 11 98765-4321) lives at 123 Rua Example"
    redacted, counts = redactor.redact(text)

    assert "[NAME]" in redacted
    assert "[EMAIL]" in redacted
    assert "[PHONE]" in redacted
    assert "[ADDRESS]" in redacted
    assert "João" not in redacted
    assert "joao@email.com" not in redacted
    assert "98765-4321" not in redacted
    assert "123 Rua Example" not in redacted
    assert counts["names"] >= 1
    assert counts["emails"] == 1
    assert counts["phones"] == 1
    assert counts["addresses"] == 1


@pytest.mark.unit
def test_redact_transcript_anonymizes_speaker_names_and_preserves_structure() -> None:
    """redact_transcript strips PII in utterances and replaces real speaker names."""
    processor = TranscriptProcessor()
    transcript = processor.process_transcript(
        "[00:00:10] Interviewer: Hello!\n"
        "[00:00:15] Maria: Email me at maria@corp.br about PIX transfers.\n",
        interview_id="INT-PII",
    )
    redactor = PIIRedactor()
    cleaned, counts = redactor.redact_transcript(transcript)

    assert cleaned.segments[0].speaker == "Interviewer"
    assert cleaned.segments[1].speaker == "Participant_1"
    assert "maria@corp.br" not in cleaned.segments[1].text
    assert "[EMAIL]" in cleaned.segments[1].text
    assert counts["emails"] == 1
    assert counts["names"] >= 1
    assert cleaned.metadata["pii_redacted"] is True
