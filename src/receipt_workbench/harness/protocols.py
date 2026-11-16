from dataclasses import dataclass
from typing import Protocol

from lxml import etree

from ..invoice_data.cii import Invoice
from ..page_text.pdftext import PdfText
from ..validation.validate import Layer


class InvoiceParser(Protocol):
    flavors: tuple[str, ...]

    def __call__(self, root: etree._Element) -> Invoice: ...


class PageReader(Protocol):
    def __call__(self, data: bytes) -> PdfText: ...


class Validator(Protocol):
    def __call__(self, xml: bytes, flavor: str, profile: str, guideline: str) -> Layer: ...


@dataclass(frozen=True)
class Export:
    data: bytes
    media_type: str
    filename: str


class Exporter(Protocol):
    def __call__(self, result: dict, review_events: list[dict]) -> Export: ...
