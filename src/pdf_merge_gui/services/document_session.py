from __future__ import annotations

from ..adapters.pypdf_adapter import PdfDocumentSession


class DocumentSessionService:
    def __init__(self) -> None:
        self.session = PdfDocumentSession()

    def close(self) -> None:
        self.session.close()
