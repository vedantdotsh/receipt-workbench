"""Compare the embedded XML with the visible text of the PDF, field by field.

Statuses, deliberately more than pass/fail:

  match       the XML value was found where that field is printed
  present     the XML value is on the page, but not tied to the field's label
  mismatch    the page shows a different value for this field and the XML value
              is nowhere on the page
  ambiguous   conflicting evidence; a person has to look
  not_found   neither the value nor a labelled place for it could be located
  not_checked the comparison could not run (no text layer, unsupported input)

"Value appears somewhere on the page" is never enough for `match` on amounts,
dates and invoice numbers, because the same number may belong to another field.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from ..common.normalize import fmt_decimal, fold, squash
from ..invoice_data.cii import Invoice, V
from ..page_text.pdftext import PdfText, Row, Token

CRITICAL = {"invoice_number", "issue_date", "grand_total", "due_payable", "tax_total", "iban"}


@dataclass
class Evidence:
    page: int
    bbox: tuple[float, float, float, float]
    text: str
    role: str = "value"  # value | label | other


@dataclass
class Finding:
    key: str
    group: str  # header | parties | payment | totals | vat | lines
    bt: str | None
    status: str
    xml_value: str | None = None
    xml_path: str | None = None
    pdf_values: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    note: str | None = None
    critical: bool = False
    line: int | None = None  # 1-based position of the line in the XML
    cells: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.I)


# strong label = names exactly this field; a different value printed next to it is evidence of a mismatch.
# weak label   = often but not always this field; it can confirm a match, never prove a mismatch.
# guard        = rows that must not count as this field's label.
AMOUNT_FIELDS = {
    "line_total": (
        _rx(r"summe\s+(der\s+)?positionen|positionssumme|zwischensumme|warenwert|sub-?\s?total|sum of line"),
        _rx(r"summe\s+netto|netto\s?summe|net\s+total|total\s+net(?!\w)|gesamt\s+netto|netto\s?betrag|net\s+amount"),
        _rx(r"brutto|gross|ttc|zahl|due"),
    ),
    "tax_basis_total": (
        _rx(r"rechnungsbetrag\s+netto|gesamtbetrag\s+netto|steuerbasis|total\s+h\.?t\.?(?!\w)|taxable\s+amount|"
            r"(gesamt|summe|betrag)\s+ohne\s+(ust|mwst|umsatzsteuer)"),
        _rx(r"netto\s?betrag|gesamt\s+netto|net\s+amount|total\s+net(?!\w)|net\s+total|summe\s+netto|netto\s?summe"),
        _rx(r"brutto|gross|ttc|zahl|due"),
    ),
    "tax_total": (
        _rx(r"(summe|gesamt|total)\s+(ust|mwst|umsatzsteuer|mehrwertsteuer|vat|tva|tax)|"
            r"(ust|mwst|vat|tva)\.?[- ]?(betrag|summe|total|amount)|steuerbetrag|umsatzsteuerbetrag|steuersumme|total\s+tax"),
        _rx(r"umsatzsteuer|mehrwertsteuer|zzgl\.?\s+(ust|mwst)|plus\s+vat|\bvat\b|\bmwst\b|\bust\b|\btva\b"),
        _rx(r"id|nummer|nr\.|number|no\.|netto|net |inkl|incl|enthält|contains|basis|steuernummer"),
    ),
    "grand_total": (
        _rx(r"gesamtbetrag|brutto\s?(summe|betrag)|gesamtsumme|endbetrag|rechnungssumme|"
            r"summe\s+brutto|gesamt\s+brutto|grand\s+total|invoice\s+total|total\s+amount|total\s+gross|gross\s+(total|amount)|"
            r"total\s+t\.?t\.?c\.?|total\s+incl|gesamt\s+inkl"),
        _rx(r"rechnungsbetrag|\btotal\b|\bgesamt\b|\bsumme\b"),
        _rx(r"netto|(?<!\w)net(?!\w)|ohne|excl|h\.?t\.?(?!\w)|zwischen|sub"),
    ),
    "due_payable": (
        _rx(r"zahlbetrag|zu\s+zahlen(der\s+betrag)?|zahlungsbetrag|fälliger\s+betrag|offener\s+betrag|restbetrag|"
            r"amount\s+due|due\s+payable|balance\s+due|total\s+due|payable\s+amount|amount\s+payable|net\s+à\s+payer|"
            r"noch\s+zu\s+zahlen|überweisungsbetrag"),
        None,
        None,
    ),
    "prepaid": (
        None,
        _rx(r"anzahlung|bereits\s+(gezahlt|bezahlt|erhalten)|abschlagszahlung|vorauszahlung|prepaid|already\s+paid|paid\s+amount|deposit"),
        None,
    ),
    "allowance_total": (None, _rx(r"summe\s+(der\s+)?(rabatte|abschläge|nachlässe)|total\s+allowances?|rabatt\s*summe"), None),
    "charge_total": (None, _rx(r"summe\s+(der\s+)?zuschläge|total\s+charges?"), None),
}
# printed as a deduction as often as a positive number
SIGN_FREE = {"prepaid", "allowance_total"}

DATE_FIELDS = {
    "issue_date": (
        _rx(r"rechnungsdatum|belegdatum|ausstellungsdatum|invoice\s+date|date\s+of\s+issue|issue\s+date|issued\s+(at|on)|"
            r"date\s+de\s+facture|gutschriftsdatum|"
            r"(rechnung|gutschrift|invoice|credit\s+note)\s*(nr\.?|no\.?|#|number)\s*\S+\s+(vom|von|dated|of)(?=\s)"),
        _rx(r"\bvom\b|\bdatum\b|\bdate\b|\bissued\b"),
        _rx(r"liefer|leistung|fällig|due|deliver|bestell|order|zahl|pay|auftrag|supply|gemäß|valid|gültig|stand"),
    ),
    "due_date": (
        _rx(r"fälligkeitsdatum|fälligkeit|fällig\s+(am|bis|zum)|zahlbar\s+bis|zahlungsziel|zahlung\s+bis|due\s+date|"
            r"payment\s+due|due\s+(on|by)|remit\s+until|pay(able)?\s+(by|until)|échéance|überweisen\s+sie\s+bis(\s+zum)?"),
        _rx(r"fällig|bis\s+zum|spätestens|\bdue\b"),
        None,
    ),
    "delivery_date": (
        _rx(r"liefer-?\s*und\s+leistungsdatum|leistungsdatum|lieferdatum|date\s+of\s+delivery|delivery\s+date|"
            r"date\s+of\s+supply|leistungszeitpunkt|date\s+de\s+livraison"),
        _rx(r"liefertermin|geliefert|delivered|lieferung"),
        None,
    ),
}

INVOICE_NUMBER_LABEL = _rx(
    r"rechnungs?[- ]?(nummer|nr\.?|no\.?)|rechnung\s+(nr\.?|no\.?|#)|invoice\s*(number|no\.?|nr\.?|#)|"
    r"beleg[- ]?(nummer|nr\.?)|facture\s*(n°|no\.?|numéro)|gutschrifts?[- ]?(nummer|nr\.?)|gutschrift\s+(nr\.?|no\.?)|"
    r"credit\s+note\s*(number|no\.?|#)|korrektur\s+(nr\.?|no\.?)"
)
VAT_WORD = _rx(r"ust|mwst|umsatzsteuer|mehrwertsteuer|steuer|\bvat\b|\btva\b|\btax\b")
TOTALS_STOP = _rx(r"summe|gesamt|total|zwischensumme|netto|brutto|zahlbetrag|amount\s+due|mwst|ust|vat")


def _ev(tok: Token, role="value") -> Evidence:
    return Evidence(tok.page, tuple(round(x, 2) for x in tok.bbox), tok.raw, role)


def _row_ev(row: Row, start=0, end=None, role="value") -> Evidence:
    end = len(row.text) if end is None else end
    return Evidence(row.page, tuple(round(x, 2) for x in row.bbox_for(start, end)), row.text[start:end], role)


class Comparer:
    def __init__(self, inv: Invoice, pdf: PdfText):
        self.inv = inv
        self.pdf = pdf
        self.rows: list[Row] = list(pdf.rows())
        self.findings: list[Finding] = []
        self.line_rows: set[int] = set()  # ids of rows used by line items
        self.sign_flex = inv.is_credit_note

    def _next_row(self, row: Row) -> Row | None:
        i = self.rows.index(row)
        if i + 1 < len(self.rows):
            n = self.rows[i + 1]
            if n.page == row.page and n.top - row.bottom < 16:
                return n
        return None

    def _label_candidates(self, label: re.Pattern, guard: re.Pattern | None, kind: str):
        """(label evidence, [tokens of `kind` printed next to that label]) for each label hit.

        Only the first value after a label belongs to it; the guard is tested on the text
        from the row start up to that value, so a later phrase on the same row is ignored.
        """
        out = []
        for row in self.rows:
            if id(row) in self.line_rows:
                continue
            for m in label.finditer(row.text):
                toks = [t for t in row.tokens if t.kind == kind and t.start >= m.end()]
                if kind == "date":
                    toks = toks[:1]
                scope = row.text[:toks[0].start] if toks else row.text
                if guard and guard.search(scope):
                    continue
                if not toks and len(row.words) <= 4:
                    nxt = self._next_row(row)
                    if nxt is not None and id(nxt) not in self.line_rows:
                        x0 = row.bbox_for(m.start(), m.end())[0]
                        toks = [t for t in nxt.tokens if t.kind == kind and t.bbox[2] >= x0 - 5]
                out.append((_row_ev(row, m.start(), m.end(), "label"), toks))
        return out

    def _eq_amount(self, tok: Token, v: Decimal) -> bool:
        return v in tok.values or (self.sign_flex and -v in tok.values)

    def _add(self, f: Finding) -> Finding:
        f.critical = f.key in CRITICAL and not (f.key == "tax_total" and f.xml_value is not None
                                                and not f.xml_value.strip("0.-"))
        self.findings.append(f)
        return f

    def _base(self, key: str, group: str, v: V, shown: str | None = None) -> Finding:
        return Finding(key=key, group=group, bt=v.bt, status="not_found", xml_value=shown or v.raw, xml_path=v.xpath)

    def _typed(self, f: Finding, kind: str, strong, weak, guard, is_equal, known: set) -> None:
        """Shared decision for amounts and dates.

        A value printed next to a strong label counts against the XML only if it is not
        any other value of the same invoice (a net total next to "Summe" is not a wrong
        gross total) and the XML value is nowhere on the page.
        """
        s_c = self._label_candidates(strong, guard, kind) if strong else []
        w_c = self._label_candidates(weak, guard, kind) if weak else []
        anchored = [(lab, t) for lab, toks in s_c + w_c for t in toks if is_equal(t)]
        if anchored:
            lab, t = anchored[0]
            f.status, f.evidence = "match", [_ev(t), lab]
            return
        elsewhere = [t for t in self.pdf.tokens(kind) if is_equal(t)]

        def explained(t: Token) -> bool:
            if t.values & known:
                return True
            return any(isinstance(v, Decimal) and -v in known for v in t.values)

        contrary = [(lab, t) for lab, toks in s_c for t in toks if not explained(t)]
        if contrary and not elsewhere:
            f.status = "mismatch"
            for lab, t in contrary[:3]:
                if t.raw not in f.pdf_values:
                    f.pdf_values.append(t.raw)
                f.evidence += [_ev(t), lab]
        elif contrary and elsewhere:
            f.status, f.note = "ambiguous", "label_shows_other_value"
            f.pdf_values = list(dict.fromkeys(t.raw for _, t in contrary[:3]))
            f.evidence = [_ev(t, "other") for _, t in contrary[:2]] + [_ev(t) for t in elsewhere[:2]]
        elif elsewhere:
            f.status, f.note = "present", "value_on_page_without_label"
            f.evidence = [_ev(t) for t in elsewhere[:3]]
        else:
            f.status, f.note = "not_found", "no_label_and_value_not_on_page"

    def _known_amounts(self) -> set:
        known: set = set()
        for v in self.inv.fields.values():
            if isinstance(v.value, Decimal):
                known.add(v.value)
        for t in self.inv.tax_rows:
            for v in (t.basis, t.amount, t.rate):
                if v is not None:
                    known.add(v.value)
        for ln in self.inv.lines:
            for v in (ln.net_amount, ln.net_price, ln.quantity):
                if v is not None:
                    known.add(v.value)
        return known

    def lines(self) -> None:
        inv = self.inv
        if not inv.lines:
            return
        rows = self.rows
        row_fold = [fold(r.text) for r in rows]

        def item_like(j: int) -> bool:
            return sum(t.kind == "number" for t in rows[j].tokens) >= 2

        def block(i: int, before=1, after=2) -> str:
            """Row i plus wrapped description rows around it. A neighbour that is itself an
            item row (two or more numbers) belongs to another line and is left out."""
            parts = [row_fold[i]]
            for j in range(i + 1, i + after + 1):
                if j >= len(rows) or rows[j].page != rows[i].page or item_like(j):
                    break
                parts.append(row_fold[j])
            for j in range(i - 1, i - before - 1, -1):
                if j < 0 or rows[j].page != rows[i].page or item_like(j):
                    break
                parts.append(row_fold[j])
            return " ".join(parts)

        def overlap(words: list[str], text: str) -> float:
            """Share of description words found in text. Longer words also count as substrings,
            because adjacent columns are sometimes extracted glued together ("StkErstattung")."""
            if not words:
                return 0.0
            toks = set(text.split())
            return sum(w in toks or (len(w) >= 4 and w in text) for w in words) / len(words)

        def nums(i: int):
            return [t for t in rows[i].tokens if t.kind == "number"]

        cand = []
        for li, line in enumerate(inv.lines):
            words = [w for w in fold(line.name.raw if line.name else "").split() if len(w) >= 2]
            for ri in range(len(rows)):
                ns = nums(ri)
                if not ns:
                    continue
                has_amt = line.net_amount is not None and any(self._eq_amount(t, line.net_amount.value) for t in ns)
                has_qty = line.quantity is not None and any(line.quantity.value in t.values for t in ns)
                has_price = line.net_price is not None and any(self._eq_amount(t, line.net_price.value) for t in ns)
                ov_alone = overlap(words, row_fold[ri])
                ov_own = overlap(words, block(ri, 0, 2))
                ov = overlap(words, block(ri))
                has_gross = any(g in t.values for t in ns for g in self._gross(line))
                if has_amt and (ov >= 0.6 or has_qty or has_price or not words):
                    cand.append((4 + has_qty + has_price + 2 * ov, li, ri, ov))
                elif ov_own >= 0.6 and ov_alone > 0:
                    cand.append((1.5 * ov_alone + 0.5 * ov_own + has_qty + has_price + has_gross, li, ri, ov_own))
        cand.sort(key=lambda c: (-c[0], c[1], c[2]))
        assigned: dict[int, tuple[int, float]] = {}
        used_rows: set[int] = set()
        for score, li, ri, ov in cand:
            if li in assigned or ri in used_rows:
                continue
            assigned[li] = (ri, ov)
            used_rows.add(ri)
        self.line_rows = {id(rows[ri]) for ri in used_rows}
        all_text = " ".join(row_fold)

        for li, line in enumerate(inv.lines):
            anchor = line.net_amount or line.name
            f = Finding(key=f"line_{li + 1}", group="lines", bt="BG-25", status="not_found", line=li + 1,
                        xml_value=(line.name.raw if line.name else None), xml_path=(anchor.xpath if anchor else None))
            cells: dict[str, dict] = {}
            specs = [("net_amount", line.net_amount, True), ("quantity", line.quantity, False), ("net_price", line.net_price, True)]
            if li not in assigned:
                words = [w for w in fold(line.name.raw if line.name else "").split() if len(w) >= 2]
                desc_on_page = overlap(words, all_text) >= 0.6
                amt_on_page = line.net_amount is not None and any(
                    self._eq_amount(t, line.net_amount.value) for t in self.pdf.tokens("number"))
                if assigned and not desc_on_page and not amt_on_page:
                    f.status, f.note = "mismatch", "line_not_on_page"
                else:
                    f.note = "line_not_located"
                for name, v, _ in specs:
                    if v is not None:
                        cells[name] = {"xml": v.raw, "status": "not_found", "pdf": []}
                f.cells = cells
                self._add(f)
                continue
            ri, ov = assigned[li]
            row = rows[ri]
            ns = nums(ri)
            explained: set[int] = set()
            reused = False

            def matches(v, signed):
                return [t for t in ns if (self._eq_amount(t, v.value) if signed else v.value in t.values)]

            def width(t):
                return (round(t.bbox[2], 1), t.end - t.start)

            # amount: the rightmost number that equals it; then quantity and price from what is left
            for name, v, signed in specs:
                if v is None:
                    continue
                cands = matches(v, signed)
                fresh = [t for t in cands if id(t) not in explained]
                pool = fresh or cands  # the same printed number may serve two cells (qty 1, price = amount)
                if pool:
                    hit = max(pool, key=width) if name == "net_amount" else min(pool, key=lambda t: t.start)
                    reused = reused or not fresh
                    explained.add(id(hit))
                    cells[name] = {"xml": v.raw, "status": "match", "pdf": [hit.raw]}
                    f.evidence.append(_ev(hit))
                else:
                    cells[name] = {"xml": v.raw, "status": "not_found", "pdf": []}
            gross = self._gross(line)
            amt = cells.get("net_amount")
            if amt and amt["status"] == "not_found" and gross:
                g = next((t for t in ns if t.values & gross), None)
                if g is not None:
                    explained.add(id(g))
                    amt.update(status="match", pdf=[g.raw], note="printed_gross")
                    f.evidence.append(_ev(g))
            side_values = set(gross)  # numbers a row may legitimately show besides qty, price and amount
            if line.vat_rate is not None:
                side_values.add(line.vat_rate.value)
                if line.net_amount is not None:
                    side_values.add((line.net_amount.value * line.vat_rate.value / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            unexplained = []
            for t in ns:
                if id(t) in explained or t.values & side_values:
                    continue
                if line.line_id is not None and t.raw == line.line_id.raw:
                    continue
                if any(o is not t and t.start >= o.start and t.end <= o.end and id(o) in explained for o in ns):
                    continue
                unexplained.append(t)
            # Is the printed line amount a different number?
            #   certain row (quantity and price both found in it): the rightmost number must be the amount
            #   likely row (matched by description): only if the XML amount is nowhere on the page
            if amt and unexplained and ov >= 0.6:
                last = max(ns, key=width)
                qty_ok = cells.get("quantity", {}).get("status") == "match"
                price_ok = cells.get("net_price", {}).get("status") == "match"
                certain = qty_ok and price_ok
                on_page = any(self._eq_amount(t, line.net_amount.value) for t in self.pdf.tokens("number"))
                stray = last in unexplained and not (last.values & self._known_amounts())
                if stray and ((amt["status"] == "not_found" and (certain or not on_page))
                              or (amt["status"] == "match" and reused and certain)):
                    amt.update(status="mismatch", pdf=[last.raw])
                    f.evidence.append(_ev(last, "other"))
            statuses = {c["status"] for c in cells.values()}
            if "mismatch" in statuses:
                f.status = "mismatch"
                f.pdf_values = [p for c in cells.values() if c["status"] == "mismatch" for p in c["pdf"]]
            elif cells.get("net_amount", {}).get("status") == "match":
                f.status = "match"
            elif "match" in statuses:
                f.status, f.note = "present", "line_located_amount_not_confirmed"
            else:
                f.status, f.note = "not_found", "line_located_no_values_confirmed"
            f.evidence.append(_row_ev(row, role="label"))
            f.cells = cells
            self._add(f)

        self._unlisted_rows(used_rows)

    @staticmethod
    def _gross(line) -> set:
        """Line amount and unit price including VAT, for layouts that print gross prices."""
        out = set()
        if line.vat_rate is None:
            return out
        factor = 1 + line.vat_rate.value / 100
        for v in (line.net_amount, line.net_price):
            if v is not None:
                out.add((v.value * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return out

    def _unlisted_rows(self, used_rows: set[int]) -> None:
        """Rows inside the item table that look like an item but belong to no XML line."""
        if len(used_rows) < 1:
            return
        rows = self.rows
        known: set[Decimal] = set()
        for v in self.inv.fields.values():
            if isinstance(v.value, Decimal):
                known.add(v.value)
        for t in self.inv.tax_rows:
            for v in (t.basis, t.amount):
                if v is not None:
                    known.add(v.value)
        for ln in self.inv.lines:
            for v in (ln.net_amount, ln.net_price, ln.quantity):
                if v is not None:
                    known.add(v.value)
        lo, hi = min(used_rows), max(used_rows)
        right_edge = max(max((t.bbox[2] for t in rows[r].tokens if t.kind == "number"), default=0) for r in used_rows)
        page = rows[lo].page
        n = 0
        for ri in range(lo, min(hi + 7, len(rows))):
            row = rows[ri]
            if row.page != page or ri in used_rows:
                continue
            if ri > hi and TOTALS_STOP.search(row.text):
                break
            ns = [t for t in row.tokens if t.kind == "number"]
            if len(ns) < 2 or abs(max(t.bbox[2] for t in ns) - right_edge) > 12:
                continue
            last = max(ns, key=lambda t: t.bbox[2])
            if any(v in last.values or -v in last.values for v in known):
                continue
            if not re.search(r"[A-Za-zÄÖÜäöü]{3,}", row.text):
                continue
            n += 1
            self._add(Finding(key=f"unlisted_row_{n}", group="lines", bt=None, status="ambiguous",
                              note="visible_row_not_in_data", pdf_values=[row.text[:120]],
                              evidence=[_row_ev(row, role="other")]))

    def amounts(self) -> None:
        known = self._known_amounts()
        multi_rate = len(self.inv.tax_rows) > 1
        for key, (strong, weak, guard) in AMOUNT_FIELDS.items():
            v = self.inv.fields.get(key)
            if v is None:
                continue
            if key in ("prepaid", "allowance_total", "charge_total") and v.value == 0:
                continue  # a zero that is not printed is not a finding
            if key == "tax_total" and multi_rate:
                strong, weak = None, _rx(f"{strong.pattern}|{weak.pattern}")  # a rate row shows a part, not the total
            f = self._base(key, "totals", v)
            free = key in SIGN_FREE

            def eq(t, vv=v.value, free=free):
                return self._eq_amount(t, vv) or (free and -vv in t.values)

            self._typed(f, "number", strong, weak, guard, eq, known - {v.value})
            self._add(f)

    def dates(self) -> None:
        known = {v.value for k, v in self.inv.fields.items() if k in DATE_FIELDS}
        for key, (strong, weak, guard) in DATE_FIELDS.items():
            v = self.inv.fields.get(key)
            if v is None:
                continue
            f = self._base(key, "header", v, v.value.isoformat())
            self._typed(f, "date", strong, weak, guard, lambda t, vv=v.value: vv in t.values, known - {v.value})
            self._add(f)

    def _find_sequence(self, value: str):
        """Rows where `value` is printed as whole words (whitespace-insensitive)."""
        target = squash(value)
        hits = []
        if not target:
            return hits
        for row in self.rows:
            ws = [w.text.strip(".,;:()[]#") for w in row.words]
            for i in range(len(ws)):
                acc = ""
                for j in range(i, min(i + 6, len(ws))):
                    acc += squash(ws[j])
                    if acc == target:
                        hits.append(_row_ev(row, row.offsets[i][0], row.offsets[j][1]))
                        break
                    if not target.startswith(acc):
                        break
        return hits

    def invoice_number(self) -> None:
        v = self.inv.fields.get("invoice_number")
        if v is None:
            return
        f = self._base("invoice_number", "header", v)
        target = squash(v.raw)
        occurrences = self._find_sequence(v.raw)
        labelled: list[tuple[Evidence, Evidence, bool]] = []
        for row in self.rows:
            for m in INVOICE_NUMBER_LABEL.finditer(row.text):
                lab = _row_ev(row, m.start(), m.end(), "label")
                src, idx = row, [i for i, (a, _) in enumerate(row.offsets) if a >= m.end()]
                if not [i for i in idx if row.words[i].text.strip(".,;:#") != ""]:
                    nxt = self._next_row(row)
                    if nxt is None:
                        continue
                    x0 = lab.bbox[0]
                    src, idx = nxt, [i for i, w in enumerate(nxt.words) if w.x1 >= x0 - 5]
                idx = [i for i in idx if src.words[i].text.strip(".,;:#") != ""]
                if not idx:
                    continue
                acc, ok, first = "", False, idx[0]
                for i in idx[:6]:
                    acc += squash(src.words[i].text.strip(".,;:()[]#"))
                    if acc == target:
                        labelled.append((lab, _row_ev(src, src.offsets[first][0], src.offsets[i][1]), True))
                        ok = True
                        break
                if not ok:
                    w = src.words[first].text.strip(".,;:()[]#")
                    if re.search(r"\d", w):
                        labelled.append((lab, _row_ev(src, *src.offsets[first]), False))
        good = [x for x in labelled if x[2]]
        if good:
            f.status, f.evidence = "match", [good[0][1], good[0][0]]
        elif labelled and not occurrences:
            f.status = "mismatch"
            f.pdf_values = list(dict.fromkeys(e.text.strip(".,;:()[]#") for _, e, _ in labelled[:3]))
            f.evidence = [e for lab, e, _ in labelled[:2]] + [lab for lab, _, _ in labelled[:1]]
        elif labelled and occurrences:
            f.status, f.note = "ambiguous", "label_shows_other_value"
            f.pdf_values = list(dict.fromkeys(e.text for _, e, _ in labelled[:3]))
            f.evidence = [Evidence(e.page, e.bbox, e.text, "other") for _, e, _ in labelled[:2]] + occurrences[:2]
        elif occurrences:
            f.status, f.note, f.evidence = "present", "value_on_page_without_label", occurrences[:3]
        else:
            f.note = "no_label_and_value_not_on_page"
        self._add(f)

    def payment(self) -> None:
        fields = self.inv.fields
        v = fields.get("iban")
        if v is not None:
            f = self._base("iban", "payment", v)
            page = list(self.pdf.tokens("iban"))
            hit = [t for t in page if squash(v.raw) in t.values]
            plain = next((r for r in self.rows if squash(v.raw) in squash(r.text)), None)
            if hit:
                f.status, f.evidence = "match", [_ev(hit[0])]
            elif plain is not None:
                f.status, f.evidence = "match", [_row_ev(plain)]
            elif page:
                f.status = "mismatch"
                f.pdf_values = list(dict.fromkeys(next(iter(t.values)) for t in page))[:3]
                f.evidence = [_ev(t) for t in page[:3]]
            else:
                f.note = "no_iban_on_page"
            self._add(f)
        v = fields.get("bic")
        if v is not None:
            f = self._base("bic", "payment", v)
            target = squash(v.raw)
            alts = {target, target[:8]} if len(target) == 11 and target.endswith("XXX") else {target, target + "XXX"}
            found = None
            labelled = None
            for row in self.rows:
                for i, w in enumerate(row.words):
                    s = squash(w.text.strip(".,;:()"))
                    if s in alts:
                        found = _row_ev(row, *row.offsets[i])
                    elif (labelled is None and i > 0 and re.search(r"BIC|SWIFT", row.words[i - 1].text, re.I)
                          and re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", s)):
                        labelled = _row_ev(row, *row.offsets[i])
            if found:
                f.status, f.evidence = "match", [found]
            elif labelled:
                f.status, f.pdf_values, f.evidence = "mismatch", [labelled.text.strip(".,;:()")], [labelled]
            else:
                f.note = "no_bic_on_page"
            self._add(f)

    def parties(self) -> None:
        fields = self.inv.fields
        xml_vat = {re.sub(r"[\s.\-]", "", fields[k].raw.upper()) for k in ("seller_vat_id", "buyer_vat_id") if k in fields}
        page_vat = list(self.pdf.tokens("vat_id"))
        unknown = [t for t in page_vat if not (t.values & xml_vat)]
        for key in ("seller_vat_id", "buyer_vat_id"):
            v = fields.get(key)
            if v is None:
                continue
            f = self._base(key, "parties", v)
            target = re.sub(r"[\s.\-]", "", v.raw.upper())
            hit = [t for t in page_vat if target in t.values]
            plain = next((r for r in self.rows if target in re.sub(r"[\s.\-]", "", r.text.upper())), None)
            # only a different ID of the same country counts against the seller's ID:
            # other parties (ship-to, tax representative) legitimately print theirs
            rival = [t for t in unknown if next(iter(t.values))[:2] == target[:2]]
            if hit:
                f.status, f.evidence = "match", [_ev(hit[0])]
            elif plain is not None:
                f.status, f.evidence = "match", [_row_ev(plain)]
            elif rival and key == "seller_vat_id":
                f.status = "mismatch"
                f.pdf_values = list(dict.fromkeys(next(iter(t.values)) for t in rival))[:3]
                f.evidence = [_ev(t) for t in rival[:3]]
            else:
                f.note = "no_vat_id_on_page" if not page_vat else "vat_id_not_on_page"
            self._add(f)
        v = fields.get("seller_tax_number")
        if v is not None:
            f = self._base("seller_tax_number", "parties", v)
            target = re.sub(r"[^0-9A-Z]", "", v.raw.upper())
            for row in self.rows:
                if target and target in re.sub(r"[^0-9A-Z]", "", row.text.upper()):
                    f.status, f.evidence = "match", [_row_ev(row)]
                    break
            self._add(f)
        for key in ("seller_name", "buyer_name"):
            v = fields.get(key)
            if v is None:
                continue
            f = self._base(key, "parties", v)
            target = fold(v.raw)
            for i, row in enumerate(self.rows):
                here = fold(row.text)
                two = here + " " + (fold(self.rows[i + 1].text) if i + 1 < len(self.rows) else "")
                if target and re.search(rf"(^| ){re.escape(target)}( |$)", here):
                    f.status, f.evidence = "match", [_row_ev(row)]
                    break
                if target and re.search(rf"(^| ){re.escape(target)}( |$)", two) and f.status != "match":
                    f.status, f.evidence = "match", [_row_ev(row)]
            if f.status != "match":
                f.note = "name_not_on_page"
            self._add(f)

    def references(self) -> None:
        for key in ("order_reference", "buyer_reference", "payment_reference"):
            v = self.inv.fields.get(key)
            if v is None or len(squash(v.raw)) < 3:
                continue
            f = self._base(key, "header", v)
            occ = self._find_sequence(v.raw)
            if occ:
                f.status, f.note, f.evidence = "present", "value_on_page_without_label", occ[:2]
            else:
                f.note = "reference_not_on_page"
            self._add(f)
        v = self.inv.fields.get("currency")
        if v is not None:
            f = self._base("currency", "header", v)
            symbols = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "JPY": "¥"}
            text = " ".join(r.text for r in self.rows)
            code = v.raw.upper()
            other = [c for c, s in symbols.items() if c != code and (re.search(rf"\b{c}\b", text) or (len(s) == 1 and s in text))]
            if re.search(rf"\b{re.escape(code)}\b", text) or symbols.get(code, "\0") in text:
                f.status = "match"
            elif other:
                f.status, f.pdf_values = "mismatch", other
            else:
                f.note = "currency_not_on_page"
            self._add(f)

    def vat(self) -> None:
        for i, tr in enumerate(self.inv.tax_rows, start=1):
            if tr.amount is None:
                continue
            f = Finding(key=f"vat_{i}", group="vat", bt="BG-23", status="not_found",
                        xml_value=tr.amount.raw, xml_path=tr.amount.xpath)
            rate = tr.rate.value if tr.rate is not None else None
            f.cells = {"rate": fmt_decimal(rate), "basis": tr.basis.raw if tr.basis else None, "amount": tr.amount.raw}
            rate_rows = []
            if rate is not None:
                for row in self.rows:
                    if id(row) in self.line_rows:
                        continue
                    if any(t.kind == "percent" and rate in t.values for t in row.tokens):
                        rate_rows.append(row)
            hit = None
            for row in rate_rows:
                for t in row.tokens:
                    if t.kind == "number" and self._eq_amount(t, tr.amount.value):
                        hit = (row, t)
                        break
                if hit:
                    break
            elsewhere = [t for t in self.pdf.tokens("number") if self._eq_amount(t, tr.amount.value)]
            if hit:
                f.status, f.evidence = "match", [_ev(hit[1]), _row_ev(hit[0], role="label")]
            else:
                with_basis = [
                    row for row in rate_rows
                    if tr.basis is not None and any(t.kind == "number" and self._eq_amount(t, tr.basis.value) for t in row.tokens)
                    and sum(t.kind == "number" for t in row.tokens) >= 2
                ]
                known = self._known_amounts()
                lone = [
                    (row, nums[0]) for row in rate_rows
                    for nums in [[t for t in row.tokens if t.kind == "number"
                                  and not any(o is not t and o.kind == "number" and o.start <= t.start and o.end >= t.end
                                              for o in row.tokens)]]
                    if len(nums) == 1 and VAT_WORD.search(row.text) and not (nums[0].values & known)
                ]
                if lone and not elsewhere:
                    row, tok = lone[0]
                    f.status, f.pdf_values = "mismatch", [tok.raw]
                    f.evidence = [_ev(tok), _row_ev(row, role="label")]
                elif with_basis and not elsewhere:
                    row = with_basis[0]
                    f.status = "mismatch"
                    f.pdf_values = [t.raw for t in row.tokens if t.kind == "number" and not self._eq_amount(t, tr.basis.value)][:3]
                    f.evidence = [_row_ev(row, role="other")]
                elif elsewhere:
                    f.status, f.note, f.evidence = "present", "value_on_page_without_label", [_ev(t) for t in elsewhere[:2]]
                else:
                    f.note = "vat_row_not_located"
            self._add(f)

    def run(self) -> list[Finding]:
        self.lines()  # first: item rows are then excluded from totals and VAT label search
        self.invoice_number()
        self.dates()
        self.references()
        self.parties()
        self.payment()
        self.amounts()
        self.vat()
        return self.findings


GROUPS = ["header", "parties", "payment", "totals", "vat", "lines"]
STATUSES = ["match", "present", "mismatch", "ambiguous", "not_found"]


def coverage(findings: list[Finding]) -> dict:
    out = {}
    for g in GROUPS + ["all", "critical"]:
        sel = [f for f in findings if (g == "all" or (g == "critical" and f.critical) or f.group == g)]
        sel = [f for f in sel if f.xml_value is not None or f.group == "lines" and f.line]
        counts = {s: sum(f.status == s for f in sel) for s in STATUSES}
        out[g] = {"applicable": len(sel), **counts}
    return out


def verdict(findings: list[Finding], cov: dict) -> str:
    if any(f.status == "mismatch" for f in findings):
        return "difference_found"
    vat_rows = [f for f in findings if f.group == "vat"]
    vat_located = bool(vat_rows) and all(f.status in ("match", "present") for f in vat_rows)
    crit = [f for f in findings if f.critical and not (f.key == "tax_total" and f.status == "not_found" and vat_located)]
    crit_ok = all(f.status in ("match", "present") for f in crit) and any(f.key == "invoice_number" for f in crit)
    total = cov["all"]["applicable"]
    located = cov["all"]["match"] + cov["all"]["present"]
    if not crit_ok or total == 0 or located / total < 0.5 or any(f.status == "ambiguous" and f.critical for f in findings):
        return "inconclusive"
    return "no_difference_in_checked_fields"


def compare(inv: Invoice, pdf: PdfText) -> tuple[list[Finding], dict, str]:
    findings = Comparer(inv, pdf).run()
    cov = coverage(findings)
    return findings, cov, verdict(findings, cov)
