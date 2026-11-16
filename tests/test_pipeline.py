"""End-to-end checking of hybrid invoices and XML-only input."""

import pytest

from receipt_workbench.pipeline.check import check_file, needs_review
from receipt_workbench.testset import synth


@pytest.mark.parametrize("layout", ["A", "B", "C"])
def test_clean_invoice_has_no_difference(layout, hybrid):
    r = check_file("clean.pdf", hybrid(1, layout))
    assert r["status"] == "checked"
    assert [layer["outcome"] for layer in r["validation"]] == ["passed", "passed", "not_run"]
    assert r["comparison"]["verdict"] == "no_difference_in_checked_fields"
    assert not [f for f in r["comparison"]["findings"] if f["status"] == "mismatch"]
    assert not needs_review(r)


def test_xrechnung_failure_needs_review(xrechnung_xml, monkeypatch):
    from lxml import etree

    from receipt_workbench.validation.validate import Layer

    monkeypatch.setattr("receipt_workbench.validation.validate.validate_rules",
                        lambda *args: Layer("rules", "passed", "test"))
    root = etree.fromstring(xrechnung_xml)
    reference = root.find(".//ram:BuyerReference", synth.NSMAP)
    reference.getparent().remove(reference)
    result = check_file("missing.xml", etree.tostring(root))
    assert [layer["outcome"] for layer in result["validation"]] == ["passed", "passed", "failed"]
    assert result["comparison"]["verdict"] == "not_compared"
    assert needs_review(result)


def test_validation_can_be_disabled(xrechnung_xml):
    result = check_file("xrechnung.xml", xrechnung_xml, run_validation=False)
    assert result["validation"] == []


def test_verbose_cli_reports_xrechnung_skip(tmp_path, capsys):
    from receipt_workbench.cli import main

    invoice = tmp_path / "facturx.xml"
    invoice.write_bytes(synth.build_xml(synth.build_spec(synth.BASES[0])))
    assert main(["check", str(invoice), "-v"]) == 0
    output = capsys.readouterr().out
    assert "xrechnung:not_run" in output and "not an XRechnung" in output


def test_xml_only_is_validated_but_not_compared():
    spec = synth.build_spec(synth.BASES[0])
    r = check_file("x.xml", synth.build_xml(spec))
    assert r["status"] == "checked" and r["comparison"]["applicable"] is False
    assert r["comparison"]["reason"] == "xml_only" and r["comparison"]["verdict"] == "not_compared"
    assert not needs_review(r)
