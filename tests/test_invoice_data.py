"""Credit note and VAT data interpretation through the checking pipeline."""

from receipt_workbench.pipeline.check import check_file


def test_credit_note_and_two_rates_are_not_false_alarms(hybrid):
    for base in (1, 3, 6):
        r = check_file("x.pdf", hybrid(base, "A"))
        assert not [f for f in r["comparison"]["findings"] if f["status"] == "mismatch"], base
