"""The visible layer: words with positions, grouped into rows, with typed tokens.

Limit: this reads the PDF *text layer*. Text that is hidden, drawn
as an image, or covered by another object is not what a person sees. The page
image is rendered separately so a reviewer can look at the evidence.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pdfplumber

from ..common.normalize import (
    DATE_RES,
    IBAN_RE,
    NUMBER_RE,
    PERCENT_AFTER_RE,
    VAT_ID_RE,
    iban_is_valid,
    parse_date_match,
    parse_number,
    squash,
)

BBox = tuple[float, float, float, float]  # x0, top, x1, bottom in PDF points, origin top-left


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass
class Token:
    kind: str  # number | percent | date | iban | vat_id
    raw: str
    values: frozenset  # Decimal, date or str readings
    start: int
    end: int
    bbox: BBox
    page: int
    row: int  # index into PageText.rows


@dataclass
class Row:
    page: int
    index: int
    words: list[Word]
    text: str
    offsets: list[tuple[int, int]]  # char span of each word in text
    tokens: list[Token] = field(default_factory=list)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    def bbox_for(self, start: int, end: int) -> BBox:
        ws = [w for w, (a, b) in zip(self.words, self.offsets) if a < end and b > start]
        if not ws:
            ws = self.words
        return (min(w.x0 for w in ws), min(w.top for w in ws), max(w.x1 for w in ws), max(w.bottom for w in ws))

    @property
    def bbox(self) -> BBox:
        return self.bbox_for(0, len(self.text))


@dataclass
class PageText:
    number: int  # 1-based
    width: float
    height: float
    rows: list[Row]
    char_count: int


@dataclass
class PdfText:
    pages: list[PageText]

    @property
    def has_text(self) -> bool:
        return sum(p.char_count for p in self.pages) >= 20

    def rows(self):
        for p in self.pages:
            yield from p.rows

    def tokens(self, kind: str | None = None):
        for r in self.rows():
            for t in r.tokens:
                if kind is None or t.kind == kind:
                    yield t


def _group_rows(words: list[Word], page: int) -> list[Row]:
    words = sorted(words, key=lambda w: ((w.top + w.bottom) / 2, w.x0))
    lines: list[list[Word]] = []
    for w in words:
        mid = (w.top + w.bottom) / 2
        if lines:
            last = lines[-1]
            lmid = sum((x.top + x.bottom) / 2 for x in last) / len(last)
            height = max(x.bottom - x.top for x in last)
            if abs(mid - lmid) <= max(2.5, 0.45 * height):
                last.append(w)
                continue
        lines.append([w])
    rows = []
    for i, ws in enumerate(lines):
        ws.sort(key=lambda w: w.x0)
        text, offsets, pos = "", [], 0
        for w in ws:
            if text:
                text += " "
                pos += 1
            offsets.append((pos, pos + len(w.text)))
            text += w.text
            pos += len(w.text)
        rows.append(Row(page=page, index=i, words=ws, text=text, offsets=offsets))
    return rows


def _tokenize(row: Row) -> None:
    text = row.text
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    def add(kind, raw, values, a, b):
        if values:
            row.tokens.append(Token(kind, raw, frozenset(values), a, b, row.bbox_for(a, b), row.page, row.index))
            taken.append((a, b))

    upper = text.upper()
    for m in IBAN_RE.finditer(upper):
        cand = squash(m[1])
        # a grouped IBAN can swallow a following short word; trim until the checksum holds
        raw_end = m.end(1)
        parts = m[1].split(" ")
        while parts and not iban_is_valid("".join(parts)):
            raw_end -= len(parts[-1]) + (1 if len(parts) > 1 else 0)
            parts = parts[:-1]
        if parts and len("".join(parts)) >= 15:
            cand = "".join(parts)
            add("iban", text[m.start(1):raw_end], {cand}, m.start(1), raw_end)
        elif "IBAN" in upper[max(0, m.start(1) - 24):m.start(1)]:
            # labelled as an IBAN but the check digits do not hold (common in sample data):
            # keep it as printed so it can still be compared
            add("iban", text[m.start(1):m.end(1)], {cand}, m.start(1), m.end(1))
    for m in VAT_ID_RE.finditer(upper):
        if free(m.start(1), m.end(1)):
            add("vat_id", text[m.start(1):m.end(1)], {re.sub(r"[\s.\-]", "", m[1])}, m.start(1), m.end(1))
    for kind, rx in DATE_RES:
        for m in rx.finditer(text):
            if free(m.start(), m.end()):
                add("date", m[0], parse_date_match(kind, m), m.start(), m.end())
    for m in NUMBER_RE.finditer(text):
        a, b = m.start("num"), m.end("trail") if m["trail"] else m.end("num")
        if not free(a, b):
            continue
        pm = PERCENT_AFTER_RE.match(text, b)
        vals = parse_number(text[a:b])
        if pm:
            add("percent", text[a:pm.end()], vals, a, pm.end())
        else:
            add("number", text[a:b], vals, a, b)
    row.tokens.sort(key=lambda t: t.start)
    # "1 190,00": a space used as thousands separator arrives as two tokens. Add the
    # merged reading as an extra candidate without removing the separate ones.
    nums = [t for t in row.tokens if t.kind == "number"]
    for t1, t2 in zip(nums, nums[1:]):
        if t2.start - t1.end == 1 and re.fullmatch(r"[-−–‐]?\d{1,3}", t1.raw) and re.fullmatch(r"\d{3}(?:[.,]\d{1,4})?", t2.raw):
            head = t1.raw.lstrip("-−–‐")
            value = Decimal(head + t2.raw.replace(",", "."))
            merged = {-value if head != t1.raw else value}
            if head.strip("0"):
                row.tokens.append(
                    Token("number", text[t1.start:t2.end], frozenset(merged), t1.start, t2.end,
                          row.bbox_for(t1.start, t2.end), row.page, row.index)
                )
    row.tokens.sort(key=lambda t: (t.start, t.end))


def read_pdf_text(data: bytes, max_pages: int = 30) -> PdfText:
    pages: list[PageText] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for n, page in enumerate(pdf.pages[:max_pages], start=1):
            raw = page.extract_words(x_tolerance=3, y_tolerance=2, keep_blank_chars=False, use_text_flow=False)
            words = [Word(w["text"], float(w["x0"]), float(w["x1"]), float(w["top"]), float(w["bottom"])) for w in raw]
            rows = _group_rows(words, n)
            for r in rows:
                _tokenize(r)
            pages.append(PageText(n, float(page.width), float(page.height), rows, sum(len(w.text) for w in words)))
    return PdfText(pages)


def render_page_png(data: bytes, page_number: int, scale: float = 1.6) -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[page_number - 1]
        image = page.render(scale=scale).to_pil()
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    finally:
        pdf.close()


__all__ = ["PdfText", "PageText", "Row", "Token", "Word", "read_pdf_text", "render_page_png", "Decimal", "date"]
