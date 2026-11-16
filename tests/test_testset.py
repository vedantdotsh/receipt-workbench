"""Seeded XML-side mutations remain valid and detectable."""

import io
import json

import pytest
from pypdf import PdfReader

from receipt_workbench.pipeline.check import check_file
from receipt_workbench.testset import synth


def test_data_side_change_keeps_xml_valid_but_is_found(hybrid):
    r = check_file("qty.pdf", hybrid(0, "A", synth.m_line_qty, side="xml"))
    assert [layer["outcome"] for layer in r["validation"]] == ["passed", "passed", "not_run"]
    assert r["comparison"]["verdict"] == "difference_found"


@pytest.mark.parametrize("layout", synth.LAYOUTS)
def test_hybrid_embeds_fonts_and_srgb_output_intent(layout):
    spec = synth.build_spec(synth.BASES[4])
    spec["lines"] *= 12
    reader = PdfReader(io.BytesIO(synth.make_hybrid(spec, spec, layout)))
    assert len(reader.pages) > 1
    intent, = reader.root_object["/OutputIntents"]
    intent = intent.get_object()
    assert intent["/Type"] == "/OutputIntent"
    assert intent["/S"] == "/GTS_PDFA1"
    assert intent["/OutputConditionIdentifier"] == "sRGB"
    profile = intent["/DestOutputProfile"]
    assert profile["/N"] == 3
    assert profile.get_data()[16:20] == b"RGB "
    assert profile.get_data()[36:40] == b"acsp"
    for page in reader.pages:
        assert not {operator for _, operator in page.get_contents().operations} & {b"g", b"G", b"k", b"K"}
        for font in page["/Resources"]["/Font"].values():
            font = font.get_object()
            assert font["/Subtype"] == "/TrueType"
            assert font["/FontDescriptor"]["/FontFile2"].get_data()
            assert font["/ToUnicode"].get_data()
    text = "".join(page.extract_text() for page in reader.pages)
    assert spec["seller"]["street"] in text
    assert spec["seller"]["city"] in text


def test_invoice_fonts_cover_german_and_currency_characters():
    from reportlab.pdfbase.pdfmetrics import getFont

    spec = synth.build_spec(synth.BASES[0])
    synth.render_pdf(spec, "A")
    characters = json.dumps([synth.BASES, synth.SELLERS, synth.BUYERS, synth.LAYOUTS, synth.UNITS,
                             synth.MONTHS_DE, synth.MONTHS_EN], ensure_ascii=False, default=str)
    characters += "\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc\u00df\u20ac\u00b7"
    for name in ("Vera", "Vera-Bold"):
        assert all(getFont(name).face.charToGlyph.get(ord(char)) for char in characters if not char.isspace())
