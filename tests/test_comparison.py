"""Field, line and unfamiliar-layout comparison tests."""

from receipt_workbench.pipeline.check import check_file
from receipt_workbench.testset import synth


def test_changed_bank_account_is_reported_with_evidence(hybrid, findings):
    r = check_file("iban.pdf", hybrid(0, "A", synth.m_iban))
    f = findings(r)["iban"]
    assert r["comparison"]["verdict"] == "difference_found"
    assert f["status"] == "mismatch" and f["critical"]
    assert f["xml_value"] == "DE89370400440532013000" and f["pdf_values"] == ["DE27100777770209299700"]
    assert f["evidence"][0]["page"] == 1 and len(f["evidence"][0]["bbox"]) == 4
    assert f["xml_path"].endswith("IBANID")
    # the XML is untouched and still valid: validation alone would not have caught this
    assert [layer["outcome"] for layer in r["validation"]] == ["passed", "passed", "not_run"]


def test_line_amount_and_totals_differences(hybrid, findings):
    r = check_file("line.pdf", hybrid(2, "B", synth.m_line_amount))
    assert findings(r)["line_2"]["status"] == "mismatch"
    r = check_file("gross.pdf", hybrid(0, "C", synth.m_gross))
    f = findings(r)
    assert f["grand_total"]["status"] == "mismatch" and f["due_payable"]["status"] == "mismatch"


def test_unknown_layout_abstains_instead_of_passing(hybrid):
    """Layout D uses labels outside the vocabulary. A changed invoice number must not come back as 'no difference'."""
    r = check_file("d.pdf", hybrid(0, "D", synth.m_number))
    assert r["comparison"]["verdict"] in ("inconclusive", "difference_found")
