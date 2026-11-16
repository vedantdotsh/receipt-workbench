"""Published invoice business rule validation tests."""

import pytest
from lxml import etree

from receipt_workbench.pipeline.check import check_file
from receipt_workbench.testset import synth


def test_rule_violation_is_reported_with_rule_id():
    spec = synth.build_spec(synth.BASES[0])
    t = synth.totals(spec)
    spec["overrides"].update(gross=t["gross"] + 5, due=t["due"] + 5)
    r = check_file("bad.xml", synth.build_xml(spec))
    rules = next(layer for layer in r["validation"] if layer["name"] == "rules")
    assert rules["outcome"] == "failed"
    assert any("BR-CO-15" in res["rule_id"] or "BR-CO-15" in res["message"] for res in rules["results"])


def test_xrechnung_passes_kosit_layer(xrechnung_xml):
    result = check_file("xrechnung.xml", xrechnung_xml)
    layer = result["validation"][2]
    assert layer["name"] == "xrechnung" and layer["outcome"] == "passed"
    assert "2.6.0" in layer["artefact"] and "3.0.2" in layer["artefact"]
    assert "Saxon" in layer["engine"] and "saxonche" in layer["engine"]
    assert layer["artefact_sha256"] == "30e64d8bddf0bebded89e2bedab0b4856f0c4f1e852bd303e1957a7d627c0e6c"
    assert layer["results"] == []


@pytest.mark.parametrize(("element", "rule_id"), [
    ("BuyerReference", "BR-DE-15"), ("DefinedTradeContact", "BR-DE-2"),
])
def test_xrechnung_mandatory_element_is_enforced(xrechnung_xml, element, rule_id):
    root = etree.fromstring(xrechnung_xml)
    missing = root.find(f".//ram:{element}", synth.NSMAP)
    missing.getparent().remove(missing)
    result = check_file("missing.xml", etree.tostring(root))
    assert result["validation"][0]["outcome"] == "passed"
    assert result["validation"][2]["outcome"] == "failed"
    finding = next(item for item in result["validation"][2]["results"] if item["rule_id"] == rule_id)
    assert finding["severity"] == "error"
    assert rule_id in finding["message"]
    assert finding["location"]


def test_plain_facturx_is_not_an_xrechnung():
    result = check_file("facturx.xml", synth.build_xml(synth.build_spec(synth.BASES[0])))
    layer = result["validation"][2]
    assert layer["name"] == "xrechnung" and layer["outcome"] == "not_run"
    assert "not an XRechnung" in layer["detail"]
    assert layer["results"] == []


def test_xrechnung_extension_codelist_rule_is_enforced(xrechnung_xml):
    root = etree.fromstring(xrechnung_xml)
    guideline = root.find(".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", synth.NSMAP)
    guideline.text += "#conformant#urn:xeinkauf.de:kosit:extension:xrechnung_3.0"
    root.find(".//ram:URIUniversalCommunication/ram:URIID", synth.NSMAP).set("schemeID", "INVALID")
    result = check_file("invalid-scheme.xml", etree.tostring(root))
    layer = result["validation"][2]
    assert layer["outcome"] == "failed"
    assert any(item["rule_id"] == "BR-DEX-07" and item["severity"] == "error" for item in layer["results"])


def test_xrechnung_warning_does_not_fail_layer(xrechnung_xml):
    root = etree.fromstring(xrechnung_xml)
    guideline = root.find(".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", synth.NSMAP)
    guideline.text = guideline.text.upper()
    result = check_file("older.xml", etree.tostring(root))
    layer = result["validation"][2]
    assert layer["outcome"] == "passed"
    finding = next(item for item in layer["results"] if item["rule_id"] == "BR-DE-21")
    assert finding["severity"] == "warning" and finding["location"]


def test_xrechnung_uses_bt24_not_other_identifiers(xrechnung_xml):
    root = etree.fromstring(xrechnung_xml)
    root.find(".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", synth.NSMAP).text = "urn:cen.eu:en16931:2017"
    root.find(".//ram:BusinessProcessSpecifiedDocumentContextParameter/ram:ID", synth.NSMAP).text = "xrechnung"
    assert check_file("facturx.xml", etree.tostring(root))["validation"][2]["outcome"] == "not_run"


def test_missing_xrechnung_artefact_is_validator_error(xrechnung_xml, monkeypatch):
    monkeypatch.setattr("receipt_workbench.validation.validate.XRECHNUNG_ARTEFACT", "missing.xsl")
    layer = check_file("xrechnung.xml", xrechnung_xml)["validation"][2]
    assert layer["outcome"] == "error" and layer["artefact"] == "missing.xsl"
    assert layer["detail"] and not layer["results"]
