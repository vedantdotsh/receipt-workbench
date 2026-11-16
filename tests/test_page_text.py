"""PDF text and typed token extraction tests."""

from decimal import Decimal

from receipt_workbench.page_text.pdftext import read_pdf_text
from receipt_workbench.testset import synth


def test_space_as_thousands_separator_is_read():
    pdf = read_pdf_text(synth.render_pdf(synth.build_spec(synth.BASES[5]), "D"))
    values = {v for t in pdf.tokens("number") for v in t.values}
    assert Decimal("184500.00") in values
