"""Shared parsing and formatting tests."""

from datetime import date
from decimal import Decimal

from receipt_workbench.common.normalize import iban_is_valid, parse_number


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


def test_dates():
    from receipt_workbench.common.normalize import DATE_RES, parse_date_match

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
