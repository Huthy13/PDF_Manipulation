"""Optional, page-scoped OCR adapter powered by Tesseract."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from importlib.util import find_spec
from io import BytesIO
from statistics import mean
from typing import Any

import fitz
from PIL import Image


@dataclass(frozen=True, slots=True)
class OcrWordBox:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class OcrLineBox:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    text: str
    line_boxes: tuple[OcrLineBox, ...] = field(default_factory=tuple)
    word_boxes: tuple[OcrWordBox, ...] = field(default_factory=tuple)
    confidence_summary: dict[str, float | int | None] = field(default_factory=dict)
    provider_metadata: dict[str, Any] = field(default_factory=dict)


class TesseractOcrAdapter:
    """Runs OCR per-page when explicitly enabled."""

    provider_name = "tesseract"

    def __init__(
        self,
        *,
        enabled: bool = False,
        dpi: int = 200,
        language: str = "eng",
        include_line_boxes: bool = True,
        include_word_boxes: bool = True,
    ) -> None:
        self.enabled = enabled
        self.dpi = dpi
        self.language = language
        self.include_line_boxes = include_line_boxes
        self.include_word_boxes = include_word_boxes

    @property
    def available(self) -> bool:
        return find_spec("pytesseract") is not None

    def extract_from_page(self, *, page: fitz.Page, page_number: int) -> OcrPageResult:
        """Render + OCR one page; no-op when OCR is disabled or unavailable."""
        if not self.enabled:
            return OcrPageResult(
                text="",
                provider_metadata={
                    "enabled": False,
                    "skipped": True,
                    "reason": "OCR disabled",
                    "provider_name": self.provider_name,
                },
            )

        if not self.available:
            return OcrPageResult(
                text="",
                provider_metadata={
                    "enabled": True,
                    "skipped": True,
                    "reason": "pytesseract is not installed",
                    "provider_name": self.provider_name,
                },
            )

        image_bytes = self.render_page_image(page=page)
        return self.extract_from_image_bytes(
            image_bytes=image_bytes,
            page_number=page_number,
        )

    def render_page_image(self, *, page: fitz.Page) -> bytes:
        """Render a parser/page object into PNG bytes for OCR."""
        pixmap = page.get_pixmap(dpi=self.dpi, alpha=False)
        return pixmap.tobytes("png")

    def extract_from_image_bytes(
        self,
        *,
        image_bytes: bytes,
        page_number: int,
    ) -> OcrPageResult:
        """Run OCR over already-rasterized page bytes."""
        if not self.enabled:
            return OcrPageResult(
                text="",
                provider_metadata={
                    "enabled": False,
                    "skipped": True,
                    "reason": "OCR disabled",
                    "provider_name": self.provider_name,
                    "page_number": page_number,
                },
            )

        pytesseract = import_module("pytesseract")
        image = Image.open(BytesIO(image_bytes))

        text = str(
            pytesseract.image_to_string(
                image,
                lang=self.language,
            )
        )

        data = pytesseract.image_to_data(
            image,
            lang=self.language,
            output_type=pytesseract.Output.DICT,
        )

        word_boxes = self._word_boxes(data) if self.include_word_boxes else tuple()
        line_boxes = self._line_boxes(data) if self.include_line_boxes else tuple()

        confidences = [
            float(value)
            for value in data.get("conf", [])
            if value not in ("", "-1")
        ]

        confidence_summary = {
            "mean": round(mean(confidences), 2) if confidences else None,
            "min": round(min(confidences), 2) if confidences else None,
            "max": round(max(confidences), 2) if confidences else None,
            "samples": len(confidences),
        }

        provider_metadata = {
            "provider_name": self.provider_name,
            "provider_version": getattr(pytesseract, "__version__", "unknown"),
            "enabled": True,
            "skipped": False,
            "page_number": page_number,
            "dpi": self.dpi,
            "language": self.language,
            "raw_data": data,
        }

        return OcrPageResult(
            text=text,
            line_boxes=line_boxes,
            word_boxes=word_boxes,
            confidence_summary=confidence_summary,
            provider_metadata=provider_metadata,
        )

    def _word_boxes(self, data: dict[str, list[Any]]) -> tuple[OcrWordBox, ...]:
        words: list[OcrWordBox] = []
        for index, text in enumerate(data.get("text", [])):
            token = str(text).strip()
            if not token:
                continue
            conf = _parse_confidence(data.get("conf", []), index)
            words.append(
                OcrWordBox(
                    text=token,
                    left=int(data["left"][index]),
                    top=int(data["top"][index]),
                    width=int(data["width"][index]),
                    height=int(data["height"][index]),
                    confidence=conf,
                )
            )
        return tuple(words)

    def _line_boxes(self, data: dict[str, list[Any]]) -> tuple[OcrLineBox, ...]:
        grouped: dict[tuple[int, int, int], list[int]] = {}
        levels = data.get("level", [])
        for index, _ in enumerate(levels):
            key = (
                int(data["block_num"][index]),
                int(data["par_num"][index]),
                int(data["line_num"][index]),
            )
            grouped.setdefault(key, []).append(index)

        lines: list[OcrLineBox] = []
        for indices in grouped.values():
            tokens = [str(data["text"][i]).strip() for i in indices if str(data["text"][i]).strip()]
            if not tokens:
                continue

            left = min(int(data["left"][i]) for i in indices)
            top = min(int(data["top"][i]) for i in indices)
            right = max(int(data["left"][i]) + int(data["width"][i]) for i in indices)
            bottom = max(int(data["top"][i]) + int(data["height"][i]) for i in indices)

            line_confs = [
                conf
                for i in indices
                if (conf := _parse_confidence(data.get("conf", []), i)) is not None
            ]

            lines.append(
                OcrLineBox(
                    text=" ".join(tokens),
                    left=left,
                    top=top,
                    width=right - left,
                    height=bottom - top,
                    confidence=round(mean(line_confs), 2) if line_confs else None,
                )
            )

        return tuple(lines)


def _parse_confidence(confidences: list[Any], index: int) -> float | None:
    if index >= len(confidences):
        return None

    raw = str(confidences[index])
    if raw in {"", "-1"}:
        return None

    return float(raw)
