"""Read a Cross Industry Invoice (ZUGFeRD 2.x / Factur-X / XRechnung CII; ZUGFeRD 1.0
best effort) into a small canonical model.

Every value keeps its EN 16931 business term (BT-…) and the XPath it came from, so a
finding can point at the exact place in the data. Amounts are Decimal, never float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

from ..common.normalize import parse_xml_date
from ..intake.ingest import xml_flavor

PROFILES = [
    ("urn:factur-x.eu:1p0:minimum", "MINIMUM"),
    ("urn:zugferd.de:2p0:minimum", "MINIMUM"),
    ("urn:factur-x.eu:1p0:basicwl", "BASIC WL"),
    ("urn:zugferd.de:2p0:basicwl", "BASIC WL"),
    ("xrechnung", "XRECHNUNG"),
    ("#compliant#urn:factur-x.eu:1p0:basic", "BASIC"),
    ("#compliant#urn:zugferd.de:2p0:basic", "BASIC"),
    ("#conformant#urn:factur-x.eu:1p0:extended", "EXTENDED"),
    ("#conformant#urn:zugferd.de:2p0:extended", "EXTENDED"),
    ("urn:ferd:crossindustrydocument:invoice:1p0:extended", "ZF1 EXTENDED"),
    ("urn:ferd:crossindustrydocument:invoice:1p0:comfort", "ZF1 COMFORT"),
    ("urn:ferd:crossindustrydocument:invoice:1p0:basic", "ZF1 BASIC"),
    ("urn:ferd:invoice:1.0:basic", "ZF1 BASIC"),
    ("urn:ferd:invoice:1.0:comfort", "ZF1 COMFORT"),
    ("urn:ferd:invoice:1.0:extended", "ZF1 EXTENDED"),
    ("urn:ferd:invoice:rc:basic", "ZF1 BASIC"),
    ("urn:ferd:invoice:rc:comfort", "ZF1 COMFORT"),
    ("urn:ferd:invoice:rc:extended", "ZF1 EXTENDED"),
    ("urn:cen.eu:en16931:2017", "EN 16931"),
]
PROFILES_WITHOUT_LINES = {"MINIMUM", "BASIC WL"}


class CiiParser:
    flavors = ("cii", "zugferd1")

    def __call__(self, root: etree._Element) -> Invoice:
        return parse_cii(root)


@dataclass
class V:
    """One value from the XML with its provenance."""

    value: object
    raw: str
    xpath: str
    bt: str


@dataclass
class TaxRow:
    category: V | None
    rate: V | None
    basis: V | None
    amount: V | None


@dataclass
class Line:
    line_id: V | None
    name: V | None
    quantity: V | None
    unit: V | None
    net_price: V | None
    net_amount: V | None
    vat_rate: V | None


@dataclass
class Invoice:
    flavor: str
    profile: str
    guideline: str
    fields: dict[str, V] = field(default_factory=dict)
    tax_rows: list[TaxRow] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)

    @property
    def is_credit_note(self) -> bool:
        tc = self.fields.get("type_code")
        return bool(tc and tc.value in {"381", "261", "262", "296", "308", "396", "420", "458", "532"})

    @property
    def has_lines_in_profile(self) -> bool:
        return self.profile not in PROFILES_WITHOUT_LINES


def _ln(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _find(el, *alts: str):
    """First descendant path match. Each alt is 'A/B/C' of local names; '|' inside a step = alternatives."""
    for alt in alts:
        nodes = [el]
        for step in alt.split("/"):
            names = step.split("|")
            nxt = []
            for n in nodes:
                nxt.extend(c for c in n if _ln(c) in names)
            nodes = nxt
            if not nodes:
                break
        if nodes:
            return nodes
    return []


class _Reader:
    def __init__(self, root):
        self.tree = root.getroottree()

    def text(self, el, bt, *alts) -> V | None:
        nodes = _find(el, *alts)
        for n in nodes:
            if (n.text or "").strip():
                raw = n.text.strip()
                return V(raw, raw, self.tree.getpath(n), bt)
        return None

    def dec(self, el, bt, *alts, pick=None) -> V | None:
        nodes = _find(el, *alts)
        if pick:
            nodes = [n for n in nodes if pick(n)] or nodes
        for n in nodes:
            raw = (n.text or "").strip()
            try:
                return V(Decimal(raw), raw, self.tree.getpath(n), bt)
            except InvalidOperation:
                continue
        return None

    def date(self, el, bt, *alts) -> V | None:
        for n in _find(el, *alts):
            raw = (n.text or "").strip()
            d = parse_xml_date(raw, n.get("format"))
            if d:
                return V(d, raw, self.tree.getpath(n), bt)
        return None


def parse_cii(root: etree._Element) -> Invoice:
    flavor = xml_flavor(root)
    if flavor not in ("cii", "zugferd1"):
        raise ValueError(f"not a CII document: {flavor}")
    r = _Reader(root)
    ctx = _find(root, "ExchangedDocumentContext|SpecifiedExchangedDocumentContext")
    guideline = ""
    if ctx:
        g = r.text(ctx[0], "BT-24", "GuidelineSpecifiedDocumentContextParameter/ID")
        guideline = g.raw if g else ""
    gl = guideline.lower()
    profile = "UNKNOWN"
    for needle, name in PROFILES:
        if needle in gl:
            profile = name
            break
    inv = Invoice(flavor=flavor, profile=profile, guideline=guideline)
    f = inv.fields

    def put(key, v):
        if v is not None:
            f[key] = v

    doc = (_find(root, "ExchangedDocument|HeaderExchangedDocument") or [root])[0]
    put("invoice_number", r.text(doc, "BT-1", "ID"))
    put("type_code", r.text(doc, "BT-3", "TypeCode"))
    put("issue_date", r.date(doc, "BT-2", "IssueDateTime/DateTimeString"))

    tx = (_find(root, "SupplyChainTradeTransaction|SpecifiedSupplyChainTradeTransaction") or [root])[0]
    agr = (_find(tx, "ApplicableHeaderTradeAgreement|ApplicableSupplyChainTradeAgreement") or [tx])[0]
    dlv = (_find(tx, "ApplicableHeaderTradeDelivery|ApplicableSupplyChainTradeDelivery") or [tx])[0]
    stl = (_find(tx, "ApplicableHeaderTradeSettlement|ApplicableSupplyChainTradeSettlement") or [tx])[0]

    put("buyer_reference", r.text(agr, "BT-10", "BuyerReference"))
    put("order_reference", r.text(agr, "BT-13", "BuyerOrderReferencedDocument/IssuerAssignedID|ID"))
    put("seller_name", r.text(agr, "BT-27", "SellerTradeParty/Name"))
    put("buyer_name", r.text(agr, "BT-44", "BuyerTradeParty/Name"))
    for party, key_vat, key_tax, bts in (("SellerTradeParty", "seller_vat_id", "seller_tax_number", ("BT-31", "BT-32")),
                                         ("BuyerTradeParty", "buyer_vat_id", None, ("BT-48", None))):
        for reg in _find(agr, f"{party}/SpecifiedTaxRegistration/ID"):
            raw = (reg.text or "").strip()
            if not raw:
                continue
            scheme = reg.get("schemeID")
            if scheme == "VA":
                f.setdefault(key_vat, V(raw, raw, r.tree.getpath(reg), bts[0]))
            elif scheme == "FC" and key_tax:
                f.setdefault(key_tax, V(raw, raw, r.tree.getpath(reg), bts[1]))

    put("delivery_date", r.date(dlv, "BT-72", "ActualDeliverySupplyChainEvent/OccurrenceDateTime/DateTimeString"))
    put("currency", r.text(stl, "BT-5", "InvoiceCurrencyCode"))
    put("payment_reference", r.text(stl, "BT-83", "PaymentReference"))
    put("iban", r.text(stl, "BT-84", "SpecifiedTradeSettlementPaymentMeans/PayeePartyCreditorFinancialAccount/IBANID"))
    put("bic", r.text(stl, "BT-86", "SpecifiedTradeSettlementPaymentMeans/PayeeSpecifiedCreditorFinancialInstitution/BICID"))
    put("due_date", r.date(stl, "BT-9", "SpecifiedTradePaymentTerms/DueDateDateTime/DateTimeString"))

    summ = (_find(stl, "SpecifiedTradeSettlementHeaderMonetarySummation|SpecifiedTradeSettlementMonetarySummation") or [stl])[0]
    cur = f["currency"].value if "currency" in f else None
    put("line_total", r.dec(summ, "BT-106", "LineTotalAmount"))
    put("allowance_total", r.dec(summ, "BT-107", "AllowanceTotalAmount"))
    put("charge_total", r.dec(summ, "BT-108", "ChargeTotalAmount"))
    put("tax_basis_total", r.dec(summ, "BT-109", "TaxBasisTotalAmount"))
    put("tax_total", r.dec(summ, "BT-110", "TaxTotalAmount", pick=lambda n: n.get("currencyID") in (None, cur)))
    put("grand_total", r.dec(summ, "BT-112", "GrandTotalAmount"))
    put("prepaid", r.dec(summ, "BT-113", "TotalPrepaidAmount"))
    put("due_payable", r.dec(summ, "BT-115", "DuePayableAmount"))

    for tax in _find(stl, "ApplicableTradeTax"):
        inv.tax_rows.append(TaxRow(
            category=r.text(tax, "BT-118", "CategoryCode"),
            rate=r.dec(tax, "BT-119", "RateApplicablePercent|ApplicablePercent"),
            basis=r.dec(tax, "BT-116", "BasisAmount"),
            amount=r.dec(tax, "BT-117", "CalculatedAmount"),
        ))

    for item in _find(tx, "IncludedSupplyChainTradeLineItem"):
        name = r.text(item, "BT-153", "SpecifiedTradeProduct/Name")
        amount = r.dec(item, "BT-131",
                       "SpecifiedLineTradeSettlement|SpecifiedSupplyChainTradeSettlement/SpecifiedTradeSettlementLineMonetarySummation|SpecifiedTradeSettlementMonetarySummation/LineTotalAmount")
        if name is None and amount is None:
            continue  # ZUGFeRD 1.0 text-only line
        qty_nodes = _find(item, "SpecifiedLineTradeDelivery|SpecifiedSupplyChainTradeDelivery/BilledQuantity")
        unit = None
        if qty_nodes and qty_nodes[0].get("unitCode"):
            unit = V(qty_nodes[0].get("unitCode"), qty_nodes[0].get("unitCode"), r.tree.getpath(qty_nodes[0]) + "/@unitCode", "BT-130")
        inv.lines.append(Line(
            line_id=r.text(item, "BT-126", "AssociatedDocumentLineDocument/LineID"),
            name=name,
            quantity=r.dec(item, "BT-129", "SpecifiedLineTradeDelivery|SpecifiedSupplyChainTradeDelivery/BilledQuantity"),
            unit=unit,
            net_price=r.dec(item, "BT-146", "SpecifiedLineTradeAgreement|SpecifiedSupplyChainTradeAgreement/NetPriceProductTradePrice/ChargeAmount"),
            net_amount=amount,
            vat_rate=r.dec(item, "BT-152",
                           "SpecifiedLineTradeSettlement|SpecifiedSupplyChainTradeSettlement/ApplicableTradeTax/RateApplicablePercent|ApplicablePercent"),
        ))
    return inv


__all__ = ["Invoice", "Line", "TaxRow", "V", "parse_cii", "date"]
