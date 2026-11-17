"""Seeded mismatch test set: hybrid invoices whose page and data are built from two
separately controlled copies of the same invoice, so one side can be changed on purpose.

Parties are fictional; tax identifiers are deliberately invalid. Bank details are
example data, not payment instructions. Each file gets a label saying what was
changed, on which side, and which fields a checker should report.

Known weakness, stated up front: the generator and the checker have the same author, so
agreement on these files is weaker evidence than results on third-party invoices. Layout
"D" deliberately uses labels the checker's vocabulary was not written for.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from lxml import etree

RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
NSMAP = {"rsm": RSM, "ram": RAM, "udt": UDT}
D = Decimal
CENT = D("0.01")


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


# All VAT IDs (including ALT_VAT) deliberately fail ISO 7064 MOD 11,10.
# Domestic tax numbers use an impossible all-zero tax-office prefix.
SELLERS = [
    dict(name="Nordlicht Bürotechnik GmbH", street="Hafenstraße 14", zip="20457", city="Hamburg", country="DE",
         vat_id="DE811907981", tax_number="00/000/00001", iban="DE89370400440532013000", bic="COBADEFFXXX", bank="Commerzbank"),
    dict(name="Alpenblick Software AG", street="Lindwurmstraße 88", zip="80337", city="München", country="DE",
         vat_id="DE129273399", tax_number="000/000/00002", iban="DE02120300000000202051", bic="BYLADEM1001", bank="DKB"),
    dict(name="Rheinwerk Montage KG", street="Deutzer Freiheit 3", zip="50679", city="Köln", country="DE",
         vat_id="DE260543044", tax_number="000/0000/0003", iban="DE02500105170137075030", bic="INGDDEFFXXX", bank="ING"),
    dict(name="Spreequell Catering UG", street="Oranienstraße 41", zip="10999", city="Berlin", country="DE",
         vat_id="DE297548313", tax_number="00/000/00004", iban="DE02100500000054540402", bic="BELADEBEXXX", bank="Berliner Sparkasse"),
]
BUYERS = [
    dict(name="Kontor Meridian GmbH", street="Speicherstadt 7", zip="20457", city="Hamburg", country="DE", vat_id="DE118587727"),
    dict(name="Fabrik Elster & Söhne OHG", street="Industrieweg 22", zip="04109", city="Leipzig", country="DE", vat_id="DE141158923"),
    dict(name="Stadtwerke Lindenau AöR", street="Am Wasserturm 1", zip="01067", city="Dresden", country="DE", vat_id=None),
]
ALT_IBANS = ["DE75512108001245126199", "DE27100777770209299700", "DE12500105170648489890", "DE91100000000123456789"]
ALT_VAT = ["DE204567317", "DE315902746", "DE183265500"]

L = lambda name, qty, unit, price, vat=19: dict(name=name, qty=D(str(qty)), unit=unit, price=D(str(price)), vat=D(str(vat)))  # noqa: E731

BASES = [
    dict(id="b01-simple", seller=0, buyer=0, number="RE-2026-10417", issue=date(2026, 9, 14), terms=14, order="PO-88213",
         lines=[L("Druckerpapier A4 80 g, 500 Blatt", 20, "C62", "4.85"), L("Tonerkartusche schwarz TK-510", 3, "C62", "68.90")]),
    dict(id="b02-two-rates", seller=3, buyer=1, number="2026-0931", issue=date(2026, 10, 2), terms=10, order="B-55102",
         lines=[L("Mittagsbuffet Business, pro Person", 45, "C62", "18.50", 7), L("Servicepersonal, Stunde", 12, "HUR", "32.00"),
                L("Mineralwasser 0,75 l", 60, "C62", "2.10")]),
    dict(id="b03-many-lines", seller=2, buyer=1, number="M-70455", issue=date(2026, 8, 27), terms=30, order="4500017762",
         lines=[L("Stahlträger HEB 200, Meter", "12.5", "MTR", "74.20"), L("Montagewinkel verzinkt 90x90", 240, "C62", "1.35"),
                L("Schwerlastanker M12x110", 480, "C62", "0.89"), L("Montagestunde Facharbeiter", 36, "HUR", "58.00"),
                L("Kranmiete Tagespauschale", 2, "DAY", "640.00"), L("Anfahrtspauschale Zone 2", 4, "C62", "45.00"),
                L("Brandschutzanstrich F30, Quadratmeter", 85, "MTK", "11.40"), L("Entsorgung Verpackung", 1, "C62", "120.00")]),
    dict(id="b04-credit-note", seller=0, buyer=2, number="GS-2026-0088", issue=date(2026, 10, 20), terms=0, order="PO-88019",
         type_code="381", lines=[L("Rücknahme Bürostuhl Modell Arvo", 2, "C62", "219.00"), L("Erstattung Lieferkosten", 1, "C62", "39.90")]),
    dict(id="b05-long-names", seller=1, buyer=0, number="AS-26-004912", issue=date(2026, 11, 3), terms=21, order="REQ-2291",
         lines=[L("Softwarewartung Plattform Meridian, Supportstufe Gold, Leistungszeitraum Oktober bis Dezember 2026", 3, "MON", "1450.00"),
                L("Individuelle Schnittstellenentwicklung zum Lagerverwaltungssystem inklusive Abnahme und Dokumentation", "27.5", "HUR", "128.00"),
                L("Schulung Administratoren vor Ort", 1, "DAY", "1900.00")]),
    dict(id="b06-large-amounts", seller=2, buyer=2, number="M-70502", issue=date(2026, 9, 30), terms=45, order="V-2026-311",
         lines=[L("Hallenkran 10 t, Lieferung und Montage", 1, "C62", "184500.00"), L("Kranbahnschiene A75, Meter", 64, "MTR", "212.75"),
                L("Statische Prüfung und Abnahme", 1, "C62", "7350.00")]),
    dict(id="b07-repeated-values", seller=3, buyer=0, number="2026-0977", issue=date(2026, 10, 16), terms=7, order="B-55340",
         lines=[L("Kaffeepause Vormittag", 25, "C62", "6.00", 7), L("Kaffeepause Nachmittag", 25, "C62", "6.00", 7),
                L("Raumreinigung", 1, "C62", "150.00"), L("Technikbetreuung", 1, "C62", "150.00")]),
    dict(id="b08-fractions", seller=1, buyer=1, number="AS-26-005020", issue=date(2026, 11, 9), terms=14, order="REQ-2317",
         lines=[L("Beratung Datenmigration, Stunde", "7.75", "HUR", "142.00"), L("Cloud-Speicher, Gigabyte pro Monat", 1250, "C62", "0.023"),
                L("Lizenz Analysemodul, Nutzer", 14, "C62", "39.00")]),
]

UNITS = {"de": {"C62": "Stk", "HUR": "Std", "MTR": "m", "DAY": "Tag", "MTK": "m²", "MON": "Monat"},
         "en": {"C62": "pcs", "HUR": "h", "MTR": "m", "DAY": "day", "MTK": "m²", "MON": "month"}}


def build_spec(base: dict) -> dict:
    s = dict(SELLERS[base["seller"]])
    return dict(
        id=base["id"], number=base["number"], type_code=base.get("type_code", "380"), currency="EUR",
        issue_date=base["issue"], due_date=base["issue"] + timedelta(days=base["terms"]) if base["terms"] else None,
        delivery_date=base["issue"] - timedelta(days=3), order_ref=base["order"],
        seller=s, buyer=dict(BUYERS[base["buyer"]]), iban=s["iban"], bic=s["bic"],
        lines=[dict(ln, id=str(i + 1)) for i, ln in enumerate(copy.deepcopy(base["lines"]))],
        overrides={},  # printed/declared values that deliberately disagree with the arithmetic
    )


def totals(spec: dict) -> dict:
    lines = []
    for ln in spec["lines"]:
        lines.append(ln.get("amount_override", q2(ln["qty"] * ln["price"])))
    by_rate: dict[Decimal, Decimal] = {}
    for ln, amt in zip(spec["lines"], [q2(l["qty"] * l["price"]) for l in spec["lines"]]):
        by_rate[ln["vat"]] = by_rate.get(ln["vat"], D(0)) + amt
    tax_rows = [dict(rate=r, basis=b, amount=q2(b * r / 100)) for r, b in sorted(by_rate.items())]
    net = sum((t["basis"] for t in tax_rows), D(0))
    tax = sum((t["amount"] for t in tax_rows), D(0))
    out = dict(line_amounts=lines, tax_rows=tax_rows, net=net, tax=tax, gross=net + tax, due=net + tax)
    for k, v in spec.get("overrides", {}).items():
        if k == "tax_row0":
            out["tax_rows"][0]["amount"] = v
        else:
            out[k] = v
    return out


def _el(parent, ns, tag, text=None, **attrs):
    e = etree.SubElement(parent, f"{{{ns}}}{tag}")
    if text is not None:
        e.text = str(text)
    for k, v in attrs.items():
        e.set(k, v)
    return e


def _dt(parent, tag, d: date):
    e = _el(parent, RAM, tag)
    _el(e, UDT, "DateTimeString", d.strftime("%Y%m%d"), format="102")


def _party(parent, tag, p, with_tax_number=False):
    e = _el(parent, RAM, tag)
    _el(e, RAM, "Name", p["name"])
    a = _el(e, RAM, "PostalTradeAddress")
    _el(a, RAM, "PostcodeCode", p["zip"])
    _el(a, RAM, "LineOne", p["street"])
    _el(a, RAM, "CityName", p["city"])
    _el(a, RAM, "CountryID", p["country"])
    if with_tax_number and p.get("tax_number"):
        _el(_el(e, RAM, "SpecifiedTaxRegistration"), RAM, "ID", p["tax_number"], schemeID="FC")
    if p.get("vat_id"):
        _el(_el(e, RAM, "SpecifiedTaxRegistration"), RAM, "ID", p["vat_id"], schemeID="VA")


def build_xml(spec: dict) -> bytes:
    t = totals(spec)
    root = etree.Element(f"{{{RSM}}}CrossIndustryInvoice", nsmap=NSMAP)
    ctx = _el(root, RSM, "ExchangedDocumentContext")
    _el(_el(ctx, RAM, "GuidelineSpecifiedDocumentContextParameter"), RAM, "ID", "urn:cen.eu:en16931:2017")
    doc = _el(root, RSM, "ExchangedDocument")
    _el(doc, RAM, "ID", spec["number"])
    _el(doc, RAM, "TypeCode", spec["type_code"])
    _dt(doc, "IssueDateTime", spec["issue_date"])
    tx = _el(root, RSM, "SupplyChainTradeTransaction")
    for ln, amount in zip(spec["lines"], t["line_amounts"]):
        item = _el(tx, RAM, "IncludedSupplyChainTradeLineItem")
        _el(_el(item, RAM, "AssociatedDocumentLineDocument"), RAM, "LineID", ln["id"])
        _el(_el(item, RAM, "SpecifiedTradeProduct"), RAM, "Name", ln["name"])
        _el(_el(_el(item, RAM, "SpecifiedLineTradeAgreement"), RAM, "NetPriceProductTradePrice"), RAM, "ChargeAmount", ln["price"])
        _el(_el(item, RAM, "SpecifiedLineTradeDelivery"), RAM, "BilledQuantity", ln["qty"], unitCode=ln["unit"])
        st = _el(item, RAM, "SpecifiedLineTradeSettlement")
        tax = _el(st, RAM, "ApplicableTradeTax")
        _el(tax, RAM, "TypeCode", "VAT")
        _el(tax, RAM, "CategoryCode", "S")
        _el(tax, RAM, "RateApplicablePercent", ln["vat"])
        _el(_el(st, RAM, "SpecifiedTradeSettlementLineMonetarySummation"), RAM, "LineTotalAmount", amount)
    agr = _el(tx, RAM, "ApplicableHeaderTradeAgreement")
    _party(agr, "SellerTradeParty", spec["seller"], with_tax_number=True)
    _party(agr, "BuyerTradeParty", spec["buyer"])
    if spec.get("order_ref"):
        _el(_el(agr, RAM, "BuyerOrderReferencedDocument"), RAM, "IssuerAssignedID", spec["order_ref"])
    dlv = _el(tx, RAM, "ApplicableHeaderTradeDelivery")
    _dt(_el(dlv, RAM, "ActualDeliverySupplyChainEvent"), "OccurrenceDateTime", spec["delivery_date"])
    stl = _el(tx, RAM, "ApplicableHeaderTradeSettlement")
    _el(stl, RAM, "InvoiceCurrencyCode", spec["currency"])
    pm = _el(stl, RAM, "SpecifiedTradeSettlementPaymentMeans")
    _el(pm, RAM, "TypeCode", "58")
    _el(_el(pm, RAM, "PayeePartyCreditorFinancialAccount"), RAM, "IBANID", spec["iban"])
    _el(_el(pm, RAM, "PayeeSpecifiedCreditorFinancialInstitution"), RAM, "BICID", spec["bic"])
    for row in t["tax_rows"]:
        tax = _el(stl, RAM, "ApplicableTradeTax")
        _el(tax, RAM, "CalculatedAmount", row["amount"])
        _el(tax, RAM, "TypeCode", "VAT")
        _el(tax, RAM, "BasisAmount", row["basis"])
        _el(tax, RAM, "CategoryCode", "S")
        _el(tax, RAM, "RateApplicablePercent", row["rate"])
    terms = _el(stl, RAM, "SpecifiedTradePaymentTerms")
    if spec.get("due_date"):
        _dt(terms, "DueDateDateTime", spec["due_date"])
    else:
        _el(terms, RAM, "Description", "Der Betrag wird verrechnet.")
    ms = _el(stl, RAM, "SpecifiedTradeSettlementHeaderMonetarySummation")
    _el(ms, RAM, "LineTotalAmount", t["net"])
    _el(ms, RAM, "TaxBasisTotalAmount", t["net"])
    _el(ms, RAM, "TaxTotalAmount", t["tax"], currencyID=spec["currency"])
    _el(ms, RAM, "GrandTotalAmount", t["gross"])
    _el(ms, RAM, "DuePayableAmount", t["due"])
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def fmt_num(x: Decimal, style: str, places: int | None = 2) -> str:
    if places is None:
        x = x.normalize()
        s = format(x, "f")
    else:
        s = format(x.quantize(D(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")
    neg = s.startswith("-")
    s = s.lstrip("-")
    whole, _, frac = s.partition(".")
    sep, dec = {"de": (".", ","), "en": (",", "."), "space": (" ", ",")}[style]
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    out = sep.join(groups) + (dec + frac if frac else "")
    return ("-" if neg else "") + out


def fmt_date(d: date, style: str) -> str:
    return {"dmy": d.strftime("%d.%m.%Y"), "iso": d.isoformat(),
            "long_de": f"{d.day}. {MONTHS_DE[d.month - 1]} {d.year}",
            "long_en": f"{d.day} {MONTHS_EN[d.month - 1]} {d.year}"}[style]


def fmt_iban(iban: str, grouped: bool) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4)) if grouped else iban


LAYOUTS = {
    "A": dict(lang="de", num="de", date="dmy", header="pairs", iban_grouped=True, money="{} €", vat_table=False,
              title={"380": "Rechnung", "381": "Gutschrift"},
              lab=dict(number="Rechnungsnummer:", date="Rechnungsdatum:", due="Fällig am:", delivery="Lieferdatum:", order="Ihre Bestellung:",
                       net="Nettobetrag", vat="USt {} %", gross="Gesamtbetrag", due_amount="Zahlbetrag", vat_id="USt-IdNr.:", tax_no="Steuernummer:"),
              cols=[("pos", "Pos.", 30, "l"), ("name", "Bezeichnung", 230, "l"), ("qty", "Menge", 70, "r"),
                    ("price", "Einzelpreis", 80, "r"), ("amount", "Gesamt", 85, "r")]),
    "B": dict(lang="en", num="en", date="iso", header="pairs", iban_grouped=False, money="EUR {}", vat_table=True,
              title={"380": "Invoice", "381": "Credit note"},
              lab=dict(number="Invoice no.", date="Invoice date", due="Due date", delivery="Delivery date", order="Your order",
                       net="Subtotal", vat="VAT {}%", gross="Total amount", due_amount="Amount due", vat_id="VAT ID:", tax_no="Tax number:"),
              cols=[("name", "Description", 215, "l"), ("qty", "Qty", 70, "r"), ("vat", "VAT", 45, "r"),
                    ("price", "Unit price", 80, "r"), ("amount", "Amount", 85, "r")]),
    "C": dict(lang="de", num="de", date="long_de", header="sentence", iban_grouped=True, money="{} EUR", vat_table=True,
              title={"380": "Rechnung", "381": "Gutschrift"},
              lab=dict(number="Nr.", date="vom", due="Bitte überweisen Sie bis zum", delivery="Leistungsdatum", order="Bestellung",
                       net="Summe netto", vat="zzgl. USt {} %", gross="Summe brutto", due_amount="Zu zahlen", vat_id="USt-ID", tax_no="St.-Nr."),
              cols=[("qty", "Menge", 75, "r"), ("name", "Artikel", 215, "l"), ("vat", "USt", 40, "r"),
                    ("price", "Preis", 80, "r"), ("amount", "Summe", 85, "r")]),
    # D: stacked label-over-value boxes and labels outside the checker's vocabulary on purpose
    "D": dict(lang="de", num="space", date="long_en", header="stacked", iban_grouped=True, money="{}", vat_table=False,
              title={"380": "Abrechnung", "381": "Stornobeleg"},
              lab=dict(number="Dokument-ID", date="Erstellt", due="Zahlungsfrist", delivery="Ausgeführt", order="Vorgang",
                       net="Leistungen", vat="Steueranteil {} %", gross="Endsumme", due_amount="Offen", vat_id="UID", tax_no="StNr"),
              cols=[("pos", "#", 25, "l"), ("name", "Leistung", 250, "l"), ("qty", "Anzahl", 65, "r"),
                    ("price", "Satz", 75, "r"), ("amount", "Wert", 80, "r")]),
}


def render_pdf(view: dict, layout: str) -> bytes:
    """Draw the visible invoice from `view`. Nothing here reads the XML."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase.pdfmetrics import (
        getRegisteredFontNames,
        registerFont,
        stringWidth,
    )
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    for name, filename in (("Vera", "Vera.ttf"), ("Vera-Bold", "VeraBd.ttf")):
        if name not in getRegisteredFontNames():
            registerFont(TTFont(name, filename))
    st = LAYOUTS[layout]
    lab, lang = st["lab"], st["lang"]
    t = totals(view)
    W, H = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1, invariant=1, initialFontName="Vera")
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    c.setTitle(f"{st['title'][view['type_code']]} {view['number']}")
    left, right = 55, W - 55
    money = lambda x: st["money"].format(fmt_num(x, st["num"]))  # noqa: E731
    seller, buyer = view["seller"], view["buyer"]

    def footer():
        c.setFont("Vera", 7.5)
        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.drawString(left, 62, f"{seller['name']} · {seller['street']} · {seller['zip']} {seller['city']}")
        c.drawString(left, 51, f"{lab['vat_id']} {seller['vat_id']}   {lab['tax_no']} {seller['tax_number']}")
        c.drawString(left, 40, f"{seller['bank']}   IBAN {fmt_iban(view['iban'], st['iban_grouped'])}   BIC {view['bic']}")
        c.setFillColorRGB(0, 0, 0)

    y = H - 60
    c.setFont("Vera-Bold", 13)
    c.drawString(left, y, seller["name"])
    c.setFont("Vera", 8.5)
    c.drawString(left, y - 13, f"{seller['street']}, {seller['zip']} {seller['city']}")
    y -= 62
    c.setFont("Vera", 10)
    for i, line in enumerate([buyer["name"], buyer["street"], f"{buyer['zip']} {buyer['city']}"]):
        c.drawString(left, y - 13 * i, line)

    title = st["title"][view["type_code"]]
    pairs = [(lab["number"], view["number"]), (lab["date"], fmt_date(view["issue_date"], st["date"])),
             (lab["delivery"], fmt_date(view["delivery_date"], st["date"]))]
    if view.get("due_date"):
        pairs.append((lab["due"], fmt_date(view["due_date"], st["date"])))
    if view.get("order_ref"):
        pairs.append((lab["order"], view["order_ref"]))

    if st["header"] == "pairs":
        for i, (k, v) in enumerate(pairs):
            c.setFont("Vera", 9)
            c.drawString(350, y - 13 * i, k)
            c.drawRightString(right, y - 13 * i, v)
        y -= 95
        c.setFont("Vera-Bold", 15)
        c.drawString(left, y, title)
    elif st["header"] == "sentence":
        y -= 80
        c.setFont("Vera-Bold", 13)
        c.drawString(left, y, f"{title} {lab['number']} {view['number']} {lab['date']} {fmt_date(view['issue_date'], st['date'])}")
        c.setFont("Vera", 9)
        y -= 16
        c.drawString(left, y, f"{lab['delivery']} {fmt_date(view['delivery_date'], st['date'])}   {lab['order']} {view.get('order_ref') or '-'}")
    else:  # stacked boxes: label on one row, value on the row below
        y -= 70
        c.setFont("Vera-Bold", 15)
        c.drawString(left, y, title)
        y -= 26
        boxw = (right - left) / len(pairs)
        for i, (k, v) in enumerate(pairs):
            x = left + i * boxw
            c.setFont("Vera", 7.5)
            c.setFillColorRGB(0.4, 0.4, 0.4)
            c.drawString(x, y, k.upper())
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Vera", min(9.5, (boxw - 8) / stringWidth(v, "Vera", 1)))
            c.drawString(x, y - 12, v)
        y -= 14

    y -= 30
    cols = st["cols"]
    xs, x = [], left
    for _, _, w, _ in cols:
        xs.append(x)
        x += w
    c.setFont("Vera-Bold", 8.5)
    for (key, head, w, align), x0 in zip(cols, xs):
        c.drawRightString(x0 + w, y, head) if align == "r" else c.drawString(x0, y, head)
    c.line(left, y - 4, right, y - 4)
    y -= 17
    units = UNITS[lang]
    for ln, amount in zip(view["lines"], t["line_amounts"]):
        name_w = next(w for k, _, w, _ in cols if k == "name") - 8
        words, rows, cur = ln["name"].split(), [], ""
        for wd in words:
            trial = (cur + " " + wd).strip()
            if stringWidth(trial, "Vera", 9) <= name_w:
                cur = trial
            else:
                rows.append(cur)
                cur = wd
        rows.append(cur)
        if y - 12 * len(rows) < 190:
            footer()
            c.showPage()
            c.setFillColorRGB(0, 0, 0)
            c.setStrokeColorRGB(0, 0, 0)
            y = H - 70
        cells = {"pos": ln["id"], "qty": f"{fmt_num(ln['qty'], st['num'], None)} {units[ln['unit']]}",
                 "price": fmt_num(ln["price"], st["num"], max(2, -ln["price"].as_tuple().exponent)),
                 "vat": f"{fmt_num(ln['vat'], st['num'], None)}%", "amount": money(amount)}
        c.setFont("Vera", 9)
        for (key, _, w, align), x0 in zip(cols, xs):
            if key == "name":
                for i, r in enumerate(rows):
                    c.drawString(x0, y - 12 * i, r)
            elif align == "r":
                c.drawRightString(x0 + w, y, cells[key])
            else:
                c.drawString(x0, y, cells[key])
        y -= 12 * len(rows) + 5
    c.line(left, y + 6, right, y + 6)

    y -= 12
    tx = right - 230
    rows = [(lab["net"], t["net"], False)]
    if st["vat_table"] and len(t["tax_rows"]) > 1:
        rows.append(({"de": "Umsatzsteuer gesamt", "en": "VAT total"}[lang], t["tax"], False))
    else:
        for tr in t["tax_rows"]:
            rows.append((lab["vat"].format(fmt_num(tr["rate"], st["num"], None)), tr["amount"], False))
    rows.append((lab["gross"], t["gross"], True))
    rows.append((lab["due_amount"], t["due"], True))
    for label, value, bold in rows:
        c.setFont("Vera-Bold" if bold else "Vera", 9.5)
        c.drawString(tx, y, label)
        c.drawRightString(right, y, money(value))
        y -= 14
    if st["vat_table"] and len(t["tax_rows"]) > 1:
        y -= 6
        c.setFont("Vera-Bold", 8)
        heads = {"de": ("USt-Satz", "Netto", "USt-Betrag"), "en": ("VAT rate", "Net", "VAT amount")}[lang]
        for i, h in enumerate(heads):
            c.drawRightString(left + 60 + i * 90, y, h)
        c.setFont("Vera", 8.5)
        for tr in t["tax_rows"]:
            y -= 12
            c.drawRightString(left + 60, y, f"{fmt_num(tr['rate'], st['num'], None)}%")
            c.drawRightString(left + 150, y, money(tr["basis"]))
            c.drawRightString(left + 240, y, money(tr["amount"]))
    if st["header"] == "sentence" and view.get("due_date"):
        y -= 22
        c.setFont("Vera", 9)
        c.drawString(left, y, f"{lab['due']} {fmt_date(view['due_date'], st['date'])}.")
    footer()
    c.showPage()
    c.save()
    writer = PdfWriter(clone_from=buf)
    profile = DecodedStreamObject()
    profile.set_data(Path(__file__).with_name("sRGB-v2-magic.icc").read_bytes())
    profile[NameObject("/N")] = NumberObject(3)
    writer.root_object[NameObject("/OutputIntents")] = ArrayObject([DictionaryObject({
        NameObject("/Type"): NameObject("/OutputIntent"),
        NameObject("/S"): NameObject("/GTS_PDFA1"),
        NameObject("/OutputConditionIdentifier"): TextStringObject("sRGB"),
        NameObject("/RegistryName"): TextStringObject("http://www.color.org"),
        NameObject("/Info"): TextStringObject("sRGB"),
        NameObject("/DestOutputProfile"): writer._add_object(profile),
    })])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _bump(d: Decimal, by="1") -> Decimal:
    return d + D(by)


def m_iban(v, rnd):
    v["iban"] = ALT_IBANS[rnd % len(ALT_IBANS)]
    return ["iban"]


def m_bic(v, rnd):
    v["bic"] = "DEUTDEFF500" if v["bic"] != "DEUTDEFF500" else "GENODEF1S02"
    return ["bic"]


def m_number(v, rnd):
    n = v["number"]
    v["number"] = n[:-1] + str((int(n[-1]) + 3) % 10)
    return ["invoice_number"]


def m_issue_date(v, rnd):
    v["issue_date"] = v["issue_date"] + timedelta(days=31)
    return ["issue_date"]


def m_due_date(v, rnd):
    if not v.get("due_date"):
        return None
    v["due_date"] = v["due_date"] - timedelta(days=9)
    return ["due_date"]


def m_gross(v, rnd):
    t = totals(v)
    v["overrides"].update(gross=t["gross"] + 100, due=t["due"] + 100)
    return ["grand_total", "due_payable"]


def m_tax(v, rnd):
    t = totals(v)
    v["overrides"].update(tax_row0=t["tax_rows"][0]["amount"] + D("10.00"), tax=t["tax"] + D("10.00"))
    return ["tax_total", "vat_1"]


def m_line_amount(v, rnd):
    i = rnd % len(v["lines"])
    ln = v["lines"][i]
    ln["amount_override"] = q2(ln["qty"] * ln["price"]) + D("27.00")
    return [f"line_{i + 1}"]


def m_line_qty(v, rnd):
    """Quantity changed and everything recomputed: this side stays internally consistent."""
    i = rnd % len(v["lines"])
    v["lines"][i]["qty"] = v["lines"][i]["qty"] + 3
    return [f"line_{i + 1}", "line_total", "tax_basis_total", "tax_total", "grand_total", "due_payable"]


def m_extra_line(v, rnd):
    v["lines"].append(dict(id=str(len(v["lines"]) + 1), name="Expresszuschlag Sonderfahrt", qty=D(1), unit="C62",
                           price=D("185.00"), vat=v["lines"][0]["vat"]))
    return [f"line_{len(v['lines'])}", "line_total", "tax_basis_total", "tax_total", "grand_total", "due_payable"]


def m_vat_id(v, rnd):
    v["seller"]["vat_id"] = ALT_VAT[rnd % len(ALT_VAT)]
    return ["seller_vat_id"]


# (name, side changed, function). "xml" = the data is changed and the page keeps the original.
MUTATIONS = [
    ("iban", "pdf", m_iban), ("iban", "xml", m_iban), ("bic", "pdf", m_bic),
    ("invoice_number", "pdf", m_number), ("issue_date", "xml", m_issue_date), ("due_date", "pdf", m_due_date),
    ("gross_total", "pdf", m_gross), ("tax_amount", "pdf", m_tax), ("line_amount", "pdf", m_line_amount),
    ("line_quantity_recomputed", "xml", m_line_qty), ("extra_line", "xml", m_extra_line),
    ("extra_line", "pdf", m_extra_line), ("seller_vat_id", "pdf", m_vat_id),
]
PAYMENT_CRITICAL = {"iban", "gross_total", "line_quantity_recomputed", "extra_line"}


def make_hybrid(spec_xml: dict, spec_pdf: dict, layout: str) -> bytes:
    import facturx

    logging.getLogger("factur-x").setLevel(logging.ERROR)
    return facturx.generate_from_binary(render_pdf(spec_pdf, layout), build_xml(spec_xml), flavor="factur-x",
                                        level="en16931", check_xsd=True, xmp_compression=False)


def generate(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clean").mkdir(exist_ok=True)
    (out_dir / "mutated").mkdir(exist_ok=True)
    cases = []
    for bi, base in enumerate(BASES):
        for layout in LAYOUTS:
            spec = build_spec(base)
            name = f"{base['id']}_{layout}_clean.pdf"
            data = make_hybrid(spec, spec, layout)
            (out_dir / "clean" / name).write_bytes(data)
            cases.append(dict(file=f"clean/{name}", base=base["id"], layout=layout, kind="clean", mutation=None, side=None,
                              expected=[], sha256=hashlib.sha256(data).hexdigest()))
            for mi, (mname, side, fn) in enumerate(MUTATIONS):
                changed = build_spec(base)
                expected = fn(changed, bi + mi)
                if expected is None:
                    continue
                xml_spec, pdf_spec = (changed, spec) if side == "xml" else (spec, changed)
                if mname == "extra_line" and side == "pdf":
                    expected = ["line_total", "tax_basis_total", "tax_total", "grand_total", "due_payable"]
                name = f"{base['id']}_{layout}_{mname}_{side}.pdf"
                data = make_hybrid(xml_spec, pdf_spec, layout)
                (out_dir / "mutated" / name).write_bytes(data)
                cases.append(dict(file=f"mutated/{name}", base=base["id"], layout=layout, kind="mutated", mutation=mname, side=side,
                                  expected=expected, payment_critical=mname in PAYMENT_CRITICAL,
                                  sha256=hashlib.sha256(data).hexdigest()))
    manifest = dict(
        name="receipt-workbench seeded PDF/XML mismatch set", version=1,
        note="Synthetic. Parties are fictional; tax identifiers are deliberately invalid. "
             "Bank details are example data, not payment instructions. "
             "side=xml means the embedded data was changed and the page shows the original; side=pdf the reverse.",
        bases=len(BASES), layouts=list(LAYOUTS), cases=cases,
    )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest


def cmd_make(args) -> int:
    m = generate(Path(args.out))
    clean = sum(c["kind"] == "clean" for c in m["cases"])
    print(f"wrote {len(m['cases'])} files to {args.out}: {clean} clean, {len(m['cases']) - clean} with a planted difference "
          f"({m['bases']} base invoices x {len(m['layouts'])} layouts)")
    return 0
