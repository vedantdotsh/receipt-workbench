"""Seeded XML-side mutations remain valid and detectable."""

from receipt_workbench.pipeline.check import check_file
from receipt_workbench.testset import synth


def test_data_side_change_keeps_xml_valid_but_is_found(hybrid):
    r = check_file("qty.pdf", hybrid(0, "A", synth.m_line_qty, side="xml"))
    assert all(layer["outcome"] == "passed" for layer in r["validation"])
    assert r["comparison"]["verdict"] == "difference_found"
