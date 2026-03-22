"""Text normalization utilities for extraction pipeline inputs.

The normalizer is intentionally conservative: it focuses on character safety and
cross-platform consistency without rewriting content semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """Normalized text payload with trace metadata."""

    text: str
    metadata: dict[str, object] = field(default_factory=dict)


def normalize_text(
    text: str,
    *,
    include_original_text: bool = False,
    unicode_form: str = "NFC",
) -> NormalizedText:
    """Normalize extraction text while preserving meaningful line breaks.

    Steps:
    1) Normalize line endings to ``\n``.
    2) Remove null bytes and disallowed control characters.
    3) Apply conservative Unicode normalization (default: NFC).

    Args:
        text: Source text to normalize.
        include_original_text: When true, include original text in metadata.
        unicode_form: Unicode normalization form. Defaults to ``NFC``.

    Returns:
        Normalized text and metadata describing what changed.
    """

    if not text:
        metadata: dict[str, object] = {
            "line_endings_normalized": True,
            "removed_control_count": 0,
            "unicode_form": unicode_form,
        }
        if include_original_text:
            metadata["original_text"] = text
        return NormalizedText(text="", metadata=metadata)

    original_text = text
    normalized_line_endings = _normalize_line_endings(text)
    sanitized_text, removed_control_count = _remove_invalid_controls(normalized_line_endings)
    normalized_unicode = unicodedata.normalize(unicode_form, sanitized_text)

    metadata = {
        "line_endings_normalized": normalized_line_endings != original_text,
        "removed_control_count": removed_control_count,
        "unicode_form": unicode_form,
        "changed": normalized_unicode != original_text,
    }
    if include_original_text:
        metadata["original_text"] = original_text

    return NormalizedText(text=normalized_unicode, metadata=metadata)


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _remove_invalid_controls(text: str) -> tuple[str, int]:
    """Drop null bytes and non-whitespace control chars.

    Preserve ``\n`` and ``\t`` to avoid destroying layout cues from OCR/parser
    output. Other C0/C1 control chars are stripped.
    """

    clean_chars: list[str] = []
    removed = 0
    for char in text:
        codepoint = ord(char)

        if codepoint == 0:
            removed += 1
            continue

        if _is_invalid_control(char):
            removed += 1
            continue

        clean_chars.append(char)

    return "".join(clean_chars), removed


def _is_invalid_control(char: str) -> bool:
    if char in {"\n", "\t"}:
        return False
    category = unicodedata.category(char)
    return category == "Cc"
