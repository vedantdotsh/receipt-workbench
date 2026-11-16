"""Field, line and unfamiliar-layout comparison tests."""

import copy

import pytest

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


@pytest.mark.parametrize("base", [0, 1, 2, 4, 5, 6, 7])
@pytest.mark.parametrize("side", ["pdf", "xml"])
def test_unknown_layout_changed_due_date_is_inconclusive(base, side, hybrid, findings):
    result = check_file("due.pdf", hybrid(base, "D", synth.m_due_date, side), run_validation=False)
    assert result["comparison"]["verdict"] == "inconclusive"
    due = findings(result)["due_date"]
    assert due["status"] == "ambiguous" and due["note"] == "unexplained_dates_on_page"
    assert any(evidence["role"] == "other" for evidence in due["evidence"])
    assert not [f for f in result["comparison"]["findings"] if f["status"] == "mismatch"]


@pytest.mark.parametrize("base", range(8))
def test_unknown_layout_clean_invoice_keeps_verdict(base, hybrid):
    result = check_file("clean.pdf", hybrid(base, "D"), run_validation=False)
    assert result["comparison"]["verdict"] == "no_difference_in_checked_fields"


@pytest.mark.parametrize("layout", ["A", "D"])
@pytest.mark.parametrize("printed_due", [True, False])
def test_legitimate_extra_dates_never_prove_a_mismatch(layout, printed_due):
    spec = synth.build_spec(synth.BASES[0])
    spec["lines"][0]["name"] += " Order date 01.09.2026; service period 02.09.2026 to 10.09.2026"
    page = copy.deepcopy(spec)
    if not printed_due:
        page["due_date"] = None
    result = check_file("dates.pdf", synth.make_hybrid(spec, page, layout), run_validation=False)
    expected = "no_difference_in_checked_fields" if printed_due else "inconclusive"
    assert result["comparison"]["verdict"] == expected
    assert not [f for f in result["comparison"]["findings"] if f["status"] == "mismatch"]


@pytest.mark.parametrize("layout", ["A", "D"])
def test_unprinted_optional_date_without_unexplained_dates_keeps_verdict(layout):
    spec = synth.build_spec(synth.BASES[0])
    page = copy.deepcopy(spec)
    page["due_date"] = None
    result = check_file("omitted.pdf", synth.make_hybrid(spec, page, layout), run_validation=False)
    assert result["comparison"]["verdict"] == "no_difference_in_checked_fields"


def test_optional_date_label_conflict_requires_review(findings):
    spec = synth.build_spec(synth.BASES[0])
    spec["lines"][0]["name"] += " Reference 28.09.2026"
    page = copy.deepcopy(spec)
    synth.m_due_date(page, 1)
    result = check_file("conflict.pdf", synth.make_hybrid(spec, page, "A"), run_validation=False)
    due = findings(result)["due_date"]
    assert due["status"] == "ambiguous" and due["note"] == "label_shows_other_value"
    assert result["comparison"]["verdict"] == "inconclusive"
    assert not [f for f in result["comparison"]["findings"] if f["status"] == "mismatch"]
