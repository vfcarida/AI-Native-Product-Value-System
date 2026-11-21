"""
Multi-layer PII redaction for customer interview transcripts (EVO-022).

Redacts emails, phone numbers (Brazilian & international/US), CPFs,
credit card numbers, street addresses (English & Portuguese), and person names
without corrupting ordinary prose words.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

from product_value_system.discovery.transcript_processor import (
    ProcessedTranscript,
    TranscriptSegment,
)

DEFAULT_COMMON_NAMES: frozenset[str] = frozenset(
    {
        # Common Brazilian / Portuguese first names
        "João",
        "Maria",
        "José",
        "Ana",
        "Pedro",
        "Paulo",
        "Carlos",
        "Marcos",
        "Lucas",
        "Gabriel",
        "Rafael",
        "Fernanda",
        "Juliana",
        "Camila",
        "Bruno",
        "Felipe",
        "Rodrigo",
        "Mariana",
        "Thiago",
        "Beatriz",
        "Gustavo",
        "Larissa",
        "Vinicius",
        "Amanda",
        # Common English first names
        "John",
        "Jane",
        "Michael",
        "Sarah",
        "David",
        "Emily",
        "Robert",
        "Jessica",
        "Daniel",
        "Jennifer",
        "Matthew",
        "Ashley",
        "Christopher",
        "Alice",
        "Bob",
    }
)

_PROTECTED_ACRONYMS = "I|PIX|API|URL|HTTP|HTTPS|JSON|CSV|VTT|SRT|USD|BRL|SLA|SLO|SLI|JTBD|ADR"


class PIIRedactor:
    """Redact personally identifiable information from customer interview text."""

    def __init__(self, extra_names: Iterable[str] | None = None) -> None:
        self.email_pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
        self.card_pattern = re.compile(r"\b\d{4}[\s-]\d{4}[\s-]\d{4}[\s-]\d{4}\b")
        self.cpf_formatted_pattern = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
        self.cpf_labeled_pattern = re.compile(r"\b(CPF\s*[:#]?\s*)(\d{11})\b", re.IGNORECASE)

        # Phone numbers: Brazilian (+55 / (11) 98765-4321) and US/international
        self.phone_pattern = re.compile(
            r"(?:"
            r"\+?55\s*\(?\d{2}\)?\s*\d{4,5}-?\d{4}"
            r"|\(\d{2}\)\s*\d{4,5}-?\d{4}"
            r"|\+\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}"
            r"|\b\d{3}-\d{3}-\d{4}\b"
            r")(?!\d)"
        )

        # Addresses: English & Portuguese formats
        self.address_pattern = re.compile(
            r"(?:"
            r"\b\d{1,5}\s+(?:Rua|Av\.?|Avenida|Alameda|Travessa)\s+"
            r"[A-ZÀ-ÿa-zà-ÿ]+(?:\s+[A-ZÀ-ÿa-zà-ÿ]+){0,3}"
            r"|\b(?:Rua|Av\.?|Avenida|Alameda|Travessa)\s+"
            r"[A-ZÀ-ÿ][a-zà-ÿ]+(?:\s+[A-ZÀ-ÿa-zà-ÿ]+){0,3},?\s+(?:n[º°]?\s*)?\d{1,5}"
            r"|\b\d{1,5}\s+[A-ZÀ-ÿ][a-zA-ZÀ-ÿ]+(?:\s+[A-ZÀ-ÿ][a-zA-ZÀ-ÿ]+){0,3}\s+"
            r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Lane|Ln\.?)"
            r")\b",
            re.IGNORECASE,
        )

        self.intro_name_pattern = re.compile(
            r"\b(my name is|i am|meu nome [eé]|eu sou)\s+"
            r"([A-ZÀ-ÿ][a-zà-ÿ]+(?:\s+[A-ZÀ-ÿ][a-zà-ÿ]+)?)\b",
            re.IGNORECASE,
        )

        self.common_names: set[str] = set(DEFAULT_COMMON_NAMES)
        if extra_names:
            self.common_names.update(n.strip() for n in extra_names if n.strip())

        self._name_pattern = self._compile_name_pattern(self.common_names)

    @staticmethod
    def _compile_name_pattern(names: set[str]) -> re.Pattern[str]:
        escaped = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        return re.compile(
            rf"\b(?:{escaped})"
            rf"(?:\s+(?:da|de|do|dos|das)\s+[A-ZÀ-ÿ][a-zà-ÿ]+"
            rf"|\s+(?!(?:{_PROTECTED_ACRONYMS})\b)[A-ZÀ-ÿ][a-zà-ÿ]+)?\b"
        )

    def redact(self, text: str) -> tuple[str, dict[str, int]]:
        """Redact all detected PII types from ``text`` and return ``(redacted, counts)``."""
        redacted = text
        counts: dict[str, int] = {
            "emails": 0,
            "phones": 0,
            "cpfs": 0,
            "cards": 0,
            "addresses": 0,
            "names": 0,
        }

        # 1. Emails first (so local-parts with names/numbers don't collide)
        redacted, counts["emails"] = self._redact_pattern(redacted, self.email_pattern, "[EMAIL]")

        # 2. Credit cards
        redacted, counts["cards"] = self._redact_pattern(redacted, self.card_pattern, "[CARD]")

        # 3. Formatted CPFs
        redacted, cpf_formatted_count = self._redact_pattern(
            redacted, self.cpf_formatted_pattern, "[CPF]"
        )
        counts["cpfs"] += cpf_formatted_count

        # 4. Phone numbers
        redacted, counts["phones"] = self._redact_pattern(redacted, self.phone_pattern, "[PHONE]")

        # 5. Labeled unformatted CPFs (e.g. "CPF 12345678901")
        labeled_matches = list(self.cpf_labeled_pattern.finditer(redacted))
        if labeled_matches:
            counts["cpfs"] += len(labeled_matches)
            redacted = self.cpf_labeled_pattern.sub(r"\1[CPF]", redacted)

        # 6. Street addresses
        redacted, counts["addresses"] = self._redact_pattern(
            redacted, self.address_pattern, "[ADDRESS]"
        )

        # 7. Explicit self-introductions ("my name is ...")
        intro_count = 0

        def _replace_intro(match: re.Match[str]) -> str:
            nonlocal intro_count
            candidate = match.group(2)
            # Only redact capitalized names (not phrases like "I am frustrated")
            if candidate and candidate[0].isupper():
                intro_count += 1
                return f"{match.group(1)} [NAME]"
            return match.group(0)

        redacted = self.intro_name_pattern.sub(_replace_intro, redacted)
        counts["names"] += intro_count

        # 8. Common person names (word-boundary matched, capitalized)
        if self.common_names:
            redacted, name_count = self._redact_pattern(redacted, self._name_pattern, "[NAME]")
            counts["names"] += name_count

        return redacted, counts

    def redact_transcript(
        self, transcript: ProcessedTranscript
    ) -> tuple[ProcessedTranscript, dict[str, int]]:
        """Return a redacted copy of ``transcript`` along with aggregated PII counts."""
        totals: dict[str, int] = {
            "emails": 0,
            "phones": 0,
            "cpfs": 0,
            "cards": 0,
            "addresses": 0,
            "names": 0,
        }
        redacted_segments: list[TranscriptSegment] = []
        speaker_aliases: dict[str, str] = {}
        anon_idx = 1

        for seg in transcript.segments:
            redacted_text, seg_counts = self.redact(seg.text)
            for key, count in seg_counts.items():
                totals[key] += count

            redacted_speaker, spk_counts = self.redact(seg.speaker)
            if spk_counts["names"] > 0 or spk_counts["emails"] > 0:
                for key, count in spk_counts.items():
                    totals[key] += count
                if seg.speaker not in speaker_aliases:
                    speaker_aliases[seg.speaker] = f"Participant_{anon_idx}"
                    anon_idx += 1
                clean_speaker = speaker_aliases[seg.speaker]
            else:
                clean_speaker = redacted_speaker

            redacted_segments.append(
                replace(seg, speaker=clean_speaker, text=redacted_text)
            )

        redacted_transcript = replace(
            transcript,
            segments=redacted_segments,
            metadata={**transcript.metadata, "pii_redacted": True, "redaction_counts": totals},
        )
        return redacted_transcript, totals

    @staticmethod
    def _redact_pattern(
        text: str, pattern: re.Pattern[str], replacement: str
    ) -> tuple[str, int]:
        """Replace all matches of ``pattern`` with ``replacement`` and return count."""
        matches = list(pattern.finditer(text))
        count = len(matches)
        if count == 0:
            return text, 0
        for match in reversed(matches):
            text = text[: match.start()] + replacement + text[match.end() :]
        return text, count
