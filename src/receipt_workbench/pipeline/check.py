"""One file in, one evidence record out."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import version

from .. import __version__
from ..comparison.compare import compare, coverage
from ..intake.ingest import IntakeError, intake, safe_parse_xml, xml_flavor
from ..invoice_data.cii import parse_cii
from ..page_text.pdftext import read_pdf_text
from ..validation.validate import (
    Layer,
    validate_rules,
    validate_schema,
    validate_xrechnung,
)

COMPARISON_METHOD = "pdf-text-layer/1"  # bump when extraction or matching rules change


def check_file(filename: str, data: bytes, run_validation: bool = True) -> dict:
    """Never raises for bad input: a file we cannot check is a result, not a crash."""
    out: dict = {
        "tool": {"name": "receipt-workbench", "version": __version__, "comparison_method": COMPARISON_METHOD,
                 "pdfplumber": version("pdfplumber"), "pypdf": version("pypdf")},
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file": {"name": filename, "size": len(data)},
        "status": "processing_failed",
        "intake": None, "invoice": None, "validation": [], "comparison": None, "pages": [],
    }
    try:
        it = intake(filename, data)
    except IntakeError as e:
        out["status"] = "unsupported"
        out["error"] = {"code": e.code, "message": str(e)}
        return out
    out["file"]["sha256"] = it.sha256
    out["intake"] = {
        "kind": it.kind, "xml_name": it.xml_name, "notes": it.notes,
        "attachments": [{"name": a.name, "size": a.size, "sha256": a.sha256, "relationship": a.relationship,
                         "is_invoice_xml": a.is_invoice_xml} for a in it.attachments],
    }

    pdf_text = None
    if it.pdf is not None:
        try:
            pdf_text = read_pdf_text(it.pdf)
            out["pages"] = [{"number": p.number, "width": p.width, "height": p.height, "chars": p.char_count}
                            for p in pdf_text.pages]
        except Exception as e:  # the XML can still be validated
            out["intake"]["notes"].append({"code": "pdf_text_unreadable", "detail": str(e)[:300]})

    if it.xml is None:
        out["status"] = "unsupported"
        out["error"] = {"code": "no_invoice_xml",
                        "message": "This PDF has no embedded invoice XML. It is an ordinary PDF, not a hybrid e-invoice."}
        return out

    try:
        root = safe_parse_xml(it.xml)
    except IntakeError as e:
        out["status"] = "unsupported"
        out["error"] = {"code": e.code, "message": str(e)}
        return out

    flavor = xml_flavor(root)
    if flavor not in ("cii", "zugferd1"):
        out["status"] = "unsupported"
        out["error"] = {"code": f"flavor_{flavor}",
                        "message": "Only UN/CEFACT CII invoices (ZUGFeRD, Factur-X, XRechnung CII) are supported so far."
                        if flavor.startswith("ubl") else "The XML is not a recognised invoice format."}
        return out

    try:
        inv = parse_cii(root)
    except Exception as e:
        out["error"] = {"code": "xml_parse_failed", "message": str(e)[:300]}
        return out

    def val(v):
        return None if v is None else {"value": v.raw, "bt": v.bt, "xpath": v.xpath}

    out["invoice"] = {
        "flavor": flavor, "profile": inv.profile, "guideline": inv.guideline, "is_credit_note": inv.is_credit_note,
        "fields": {k: val(v) for k, v in inv.fields.items()},
        "tax_rows": [{"category": val(t.category), "rate": val(t.rate), "basis": val(t.basis), "amount": val(t.amount)}
                     for t in inv.tax_rows],
        "lines": [{"line_id": val(ln.line_id), "name": val(ln.name), "quantity": val(ln.quantity), "unit": val(ln.unit),
                   "net_price": val(ln.net_price), "net_amount": val(ln.net_amount), "vat_rate": val(ln.vat_rate)}
                  for ln in inv.lines],
    }

    if run_validation:
        layers: list[Layer] = [validate_schema(it.xml, flavor, inv.profile), validate_rules(it.xml, flavor, inv.profile),
                              validate_xrechnung(it.xml, flavor, inv.guideline)]
        out["validation"] = [layer.to_dict() for layer in layers]

    comp: dict = {"method": COMPARISON_METHOD, "applicable": True, "reason": None, "findings": [], "coverage": coverage([]),
                  "verdict": "not_compared"}
    if it.kind == "xml":
        comp.update(applicable=False, reason="xml_only")
    elif pdf_text is None:
        comp.update(applicable=False, reason="pdf_text_unreadable")
    elif not pdf_text.has_text:
        comp.update(applicable=False, reason="no_text_layer")
    else:
        findings, cov, verdict = compare(inv, pdf_text)
        comp.update(findings=[f.to_dict() for f in findings], coverage=cov, verdict=verdict)
        if not inv.has_lines_in_profile:
            comp["line_note"] = "profile_has_no_lines"
    out["comparison"] = comp
    out["status"] = "checked"
    return out


def needs_review(result: dict) -> bool:
    if result["status"] != "checked":
        return True
    if any(layer["outcome"] in ("failed", "error") for layer in result["validation"]):
        return True
    return result["comparison"]["verdict"] in ("difference_found", "inconclusive")
