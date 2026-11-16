"""End-to-end checking of hybrid invoices and XML-only input."""

import pytest

from receipt_workbench.pipeline.check import check_file
from receipt_workbench.testset import synth


@pytest.mark.parametrize("layout", ["A", "B", "C"])
def test_clean_invoice_has_no_difference(layout, hybrid):
    r = check_file("clean.pdf", hybrid(1, layout))
    assert r["status"] == "checked"
    assert [layer["outcome"] for layer in r["validation"]] == ["passed", "passed"]
    assert r["comparison"]["verdict"] == "no_difference_in_checked_fields"
    assert not [f for f in r["comparison"]["findings"] if f["status"] == "mismatch"]


def test_xml_only_is_validated_but_not_compared():
    spec = synth.build_spec(synth.BASES[0])
    r = check_file("x.xml", synth.build_xml(spec))
    assert r["status"] == "checked" and r["comparison"]["applicable"] is False
    assert r["comparison"]["reason"] == "xml_only" and r["comparison"]["verdict"] == "not_compared"
