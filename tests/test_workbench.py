"""End-to-end tests on invoices generated in the test itself (no downloaded data needed)."""

import copy
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from receipt_workbench import synth
from receipt_workbench.check import check_file
from receipt_workbench.normalize import iban_is_valid, parse_number
from receipt_workbench.pdftext import read_pdf_text


def hybrid(base=0, layout="A", mutate=None, side="pdf"):
    spec = synth.build_spec(synth.BASES[base])
    changed = copy.deepcopy(spec)
    if mutate:
        mutate(changed, 1)
    xml_spec, pdf_spec = (changed, spec) if side == "xml" else (spec, changed)
    return synth.make_hybrid(xml_spec, pdf_spec, layout)


def findings(result):
    return {f["key"]: f for f in result["comparison"]["findings"]}


def test_number_readings():
    assert parse_number("1.190,00") == {Decimal("1190.00")}
    assert parse_number("1,190.00") == {Decimal("1190.00")}
    assert parse_number("-17808,00") == {Decimal("-17808.00")}
    assert parse_number("(12,50)") == {Decimal("-12.50")}
    # ambiguous on purpose: one thousand in German, one in English
    assert parse_number("1.000") == {Decimal("1.000"), Decimal("1000")}
    assert parse_number("RE-2026") == set()


def test_iban_checksum():
    assert iban_is_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_is_valid("DE89 3704 0044 0532 0130 01")


@pytest.mark.parametrize("layout", ["A", "B", "C"])
def test_clean_invoice_has_no_difference(layout):
    r = check_file("clean.pdf", hybrid(1, layout))
    assert r["status"] == "checked"
    assert [layer["outcome"] for layer in r["validation"]] == ["passed", "passed"]
    assert r["comparison"]["verdict"] == "no_difference_in_checked_fields"
    assert not [f for f in r["comparison"]["findings"] if f["status"] == "mismatch"]


def test_changed_bank_account_is_reported_with_evidence():
    r = check_file("iban.pdf", hybrid(0, "A", synth.m_iban))
    f = findings(r)["iban"]
    assert r["comparison"]["verdict"] == "difference_found"
    assert f["status"] == "mismatch" and f["critical"]
    assert f["xml_value"] == "DE89370400440532013000" and f["pdf_values"] == ["DE27100777770209299700"]
    assert f["evidence"][0]["page"] == 1 and len(f["evidence"][0]["bbox"]) == 4
    assert f["xml_path"].endswith("IBANID")
    # the XML is untouched and still valid: validation alone would not have caught this
    assert all(layer["outcome"] == "passed" for layer in r["validation"])


def test_line_amount_and_totals_differences():
    r = check_file("line.pdf", hybrid(2, "B", synth.m_line_amount))
    assert findings(r)["line_2"]["status"] == "mismatch"
    r = check_file("gross.pdf", hybrid(0, "C", synth.m_gross))
    f = findings(r)
    assert f["grand_total"]["status"] == "mismatch" and f["due_payable"]["status"] == "mismatch"


def test_data_side_change_keeps_xml_valid_but_is_found():
    r = check_file("qty.pdf", hybrid(0, "A", synth.m_line_qty, side="xml"))
    assert all(layer["outcome"] == "passed" for layer in r["validation"])
    assert r["comparison"]["verdict"] == "difference_found"


def test_credit_note_and_two_rates_are_not_false_alarms():
    for base in (1, 3, 6):
        r = check_file("x.pdf", hybrid(base, "A"))
        assert not [f for f in r["comparison"]["findings"] if f["status"] == "mismatch"], base


def test_unknown_layout_abstains_instead_of_passing():
    """Layout D uses labels outside the vocabulary. A changed invoice number must not come back as 'no difference'."""
    r = check_file("d.pdf", hybrid(0, "D", synth.m_number))
    assert r["comparison"]["verdict"] in ("inconclusive", "difference_found")


def test_xml_only_is_validated_but_not_compared():
    spec = synth.build_spec(synth.BASES[0])
    r = check_file("x.xml", synth.build_xml(spec))
    assert r["status"] == "checked" and r["comparison"]["applicable"] is False
    assert r["comparison"]["reason"] == "xml_only" and r["comparison"]["verdict"] == "not_compared"


def test_rule_violation_is_reported_with_rule_id():
    spec = synth.build_spec(synth.BASES[0])
    t = synth.totals(spec)
    spec["overrides"].update(gross=t["gross"] + 5, due=t["due"] + 5)
    r = check_file("bad.xml", synth.build_xml(spec))
    rules = next(layer for layer in r["validation"] if layer["name"] == "rules")
    assert rules["outcome"] == "failed"
    assert any("BR-CO-15" in res["rule_id"] or "BR-CO-15" in res["message"] for res in rules["results"])


def test_hostile_and_unsupported_input_is_a_result_not_a_crash():
    xxe = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    r = check_file("xxe.xml", xxe)
    assert r["status"] == "unsupported" and r["error"]["code"] == "xml_doctype_refused"
    assert check_file("a.txt", b"hello")["error"]["code"] == "unsupported_file"
    assert check_file("empty.pdf", b"")["error"]["code"] == "file_empty"
    plain = synth.render_pdf(synth.build_spec(synth.BASES[0]), "A")
    assert check_file("plain.pdf", plain)["error"]["code"] == "no_invoice_xml"


def test_two_different_invoice_xmls_are_refused():
    import facturx

    spec = synth.build_spec(synth.BASES[0])
    other = synth.build_spec(synth.BASES[1])
    pdf = facturx.generate_from_binary(
        synth.render_pdf(spec, "A"), synth.build_xml(spec), flavor="factur-x", level="en16931",
        attachments={"invoice-copy.xml": {"filedata": synth.build_xml(other), "description": "second"}})
    r = check_file("two.pdf", pdf)
    # a standard-named attachment wins, but the extra one is reported
    assert r["status"] == "checked"
    assert any(n["code"] == "extra_invoice_xml" for n in r["intake"]["notes"])


def test_space_as_thousands_separator_is_read():
    pdf = read_pdf_text(synth.render_pdf(synth.build_spec(synth.BASES[5]), "D"))
    values = {v for t in pdf.tokens("number") for v in t.values}
    assert Decimal("184500.00") in values


def test_dates():
    from receipt_workbench.normalize import DATE_RES, parse_date_match

    def read(s):
        out = set()
        for kind, rx in DATE_RES:
            for m in rx.finditer(s):
                out |= parse_date_match(kind, m)
        return out

    assert date(2026, 3, 5) in read("vom 5. März 2026")
    assert date(2026, 3, 5) in read("05.03.2026")
    assert date(2026, 3, 5) in read("issued at 2026-03-05")
    assert read("03/05/2026") == {date(2026, 5, 3), date(2026, 3, 5)}


def test_server_flow():
    from receipt_workbench.server import ITEMS, app

    ITEMS.clear()
    c = TestClient(app)
    assert c.get("/").status_code == 200
    samples = c.post("/api/samples").json()
    assert len(samples) == 6
    by = {s["sample"]: s for s in samples}
    assert by["clean"]["verdict"] == "no_difference_in_checked_fields" and not by["clean"]["needs_review"]
    assert by["iban"]["verdict"] == "difference_found"
    assert by["line"]["verdict"] == "difference_found"
    assert by["benign"]["verdict"] == "no_difference_in_checked_fields"
    assert by["inconclusive"]["needs_review"] or by["inconclusive"]["verdict"] == "no_difference_in_checked_fields"
    assert by["rules"]["validation"]["rules"] == "failed"
    item = by["iban"]["id"]
    assert c.get(f"/api/items/{item}/page/1.png").headers["content-type"] == "image/png"
    assert c.get(f"/api/items/{item}/page/9.png").status_code == 404
    ev = c.post(f"/api/items/{item}/review", json={"decision": "needs_clarification", "note": "ask supplier"}).json()
    assert ev["events"][0]["decision"] == "needs_clarification"
    assert c.post(f"/api/items/{item}/review", json={"decision": "pay"}).status_code == 422
    bundle = c.get(f"/api/items/{item}/evidence").json()
    assert bundle["review_events"] and bundle["result"]["file"]["sha256"]
    up = c.post("/api/check", files=[("files", ("n.txt", b"x", "text/plain"))]).json()
    assert up[0]["status"] == "unsupported"
