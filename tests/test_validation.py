"""Published invoice business rule validation tests."""

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
