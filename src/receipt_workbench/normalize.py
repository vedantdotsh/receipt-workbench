"""Typed readings of printed values: amounts, dates, IBANs, percentages.

A printed token can be ambiguous ("1.000" is one thousand in German and one in
English), so every parser returns the *set* of values the token may mean. A
comparison succeeds when the XML value is in that set. Nothing here guesses.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

MINUS_CHARS = "-−–‐"

# A number as printed: optional sign or bracket, digits with . , ' separators.
# Lookarounds keep it from biting into dates (05.03.2018) or identifiers (RE-2018/7).
NUMBER_RE = re.compile(
    r"(?<![\w.,/])"
    r"(?P<num>[-−–‐+]?\(?\d{1,3}(?:[.,'’]\d{3})+(?:[.,]\d{1,6})?\)?|[-−–‐+]?\(?\d+(?:[.,]\d{1,6})?\)?)"
    r"(?P<trail>[-−–‐]?)"
    r"(?![\w/]|[.,]\d)"
)

PERCENT_AFTER_RE = re.compile(r"\s?%")

_MONTHS = {
    "januar": 1, "jan": 1, "january": 1, "janvier": 1,
    "februar": 2, "feb": 2, "february": 2, "février": 2, "fevrier": 2,
    "märz": 3, "maerz": 3, "mär": 3, "mar": 3, "march": 3, "mars": 3,
    "april": 4, "apr": 4, "avril": 4,
    "mai": 5, "may": 5,
    "juni": 6, "jun": 6, "june": 6, "juin": 6,
    "juli": 7, "jul": 7, "july": 7, "juillet": 7,
    "august": 8, "aug": 8, "août": 8, "aout": 8,
    "september": 9, "sep": 9, "sept": 9, "septembre": 9,
    "oktober": 10, "okt": 10, "october": 10, "oct": 10, "octobre": 10,
    "november": 11, "nov": 11, "novembre": 11,
    "dezember": 12, "dez": 12, "december": 12, "dec": 12, "décembre": 12, "decembre": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

DATE_RES = [
    ("ymd", re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")),
    ("dmy", re.compile(r"(?<!\d)(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}|\d{2})(?!\d)")),
    ("slash", re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")),
    ("d_mon_y", re.compile(rf"(?<!\d)(\d{{1,2}})\.?\s(?:de\s)?({_MONTH_ALT})\.?,?\s(\d{{4}})(?!\d)", re.I)),
    ("mon_d_y", re.compile(rf"(?<![A-Za-zäöüé])({_MONTH_ALT})\.?\s(\d{{1,2}}),?\s(\d{{4}})(?!\d)", re.I)),
    ("compact", re.compile(r"(?<![\d.,])(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?![\d.,])")),
]

IBAN_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?)(?![A-Z0-9])")
VAT_ID_RE = re.compile(
    r"(?<![A-Z0-9])(DE\s?\d{9}|GB\s?\d{9}|DK\s?\d{8}|SE\s?\d{12}|FI\s?\d{8}|PT\s?\d{9}|CZ\s?\d{8,10}|ATU\s?\d{8}|FR\s?[A-Z0-9]{2}\s?\d{9}|NL\s?\d{9}B\d{2}|"
    r"BE\s?0?\d{9}|IT\s?\d{11}|ES\s?[A-Z0-9]\d{7}[A-Z0-9]|PL\s?\d{10}|LU\s?\d{8}|CHE[- ]?\d{3}\.?\d{3}\.?\d{3})(?![A-Z0-9])"
)


def parse_number(token: str) -> set[Decimal]:
    """All values a printed number may mean. Empty set if it is not a number."""
    s = token.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    s = s.strip("()")
    if s and s[0] in MINUS_CHARS:
        negative, s = True, s[1:]
    elif s and s[0] == "+":
        s = s[1:]
    if s and s[-1] in MINUS_CHARS:
        negative, s = True, s[:-1]
    s = s.replace("’", "'").replace("'", "")
    if not s or not re.fullmatch(r"\d[\d.,]*", s):
        return set()
    readings: set[str] = set()
    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        readings.add(s.replace(thou, "").replace(dec, "."))
    elif dots or commas:
        sep = "." if dots else ","
        if s.count(sep) > 1:
            readings.add(s.replace(sep, ""))
        else:
            head, tail = s.split(sep)
            readings.add(f"{head}.{tail}")
            if len(tail) == 3 and 1 <= len(head) <= 3 and head != "0":
                readings.add(head + tail)  # could also be a thousands separator
    else:
        readings.add(s)
    out: set[Decimal] = set()
    for r in readings:
        try:
            d = Decimal(r)
        except InvalidOperation:
            continue
        out.add(-d if negative else d)
    return out


def _mk_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date_match(kind: str, m: re.Match) -> set[date]:
    out: set[date | None] = set()
    if kind == "ymd":
        out.add(_mk_date(int(m[1]), int(m[2]), int(m[3])))
    elif kind == "dmy":
        y = int(m[3])
        if y < 100:
            y += 2000
        out.add(_mk_date(y, int(m[2]), int(m[1])))
    elif kind == "slash":  # dd/mm or mm/dd: keep both readings
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        out.add(_mk_date(y, b, a))
        out.add(_mk_date(y, a, b))
    elif kind == "d_mon_y":
        out.add(_mk_date(int(m[3]), _MONTHS[m[2].lower()], int(m[1])))
    elif kind == "mon_d_y":
        out.add(_mk_date(int(m[3]), _MONTHS[m[1].lower()], int(m[2])))
    elif kind == "compact":
        out.add(_mk_date(int(m[1]), int(m[2]), int(m[3])))
    return {d for d in out if d is not None}


def parse_xml_date(value: str, fmt: str | None = "102") -> date | None:
    v = (value or "").strip()
    if re.fullmatch(r"\d{8}", v):
        return _mk_date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(T.*)?", v)
    if m:
        return _mk_date(int(m[1]), int(m[2]), int(m[3]))
    return None


def iban_is_valid(iban: str) -> bool:
    s = re.sub(r"\s", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


def squash(s: str) -> str:
    """Identifier comparison key: no whitespace, upper case."""
    return re.sub(r"\s+", "", s or "").upper()


def fold(s: str) -> str:
    """Loose text key for names and descriptions."""
    s = (s or "").lower()
    s = s.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return re.sub(r"[^0-9a-z]+", " ", s).strip()


def fmt_decimal(d: Decimal | None) -> str | None:
    return None if d is None else format(d, "f")
