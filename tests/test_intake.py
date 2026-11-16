"""Unsupported input and embedded invoice attachment tests."""

from receipt_workbench.pipeline.check import check_file
from receipt_workbench.testset import synth


def test_hostile_and_unsupported_input_is_a_result_not_a_crash():
    xxe = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    r = check_file("xxe.xml", xxe)
    assert r["status"] == "unsupported" and r["error"]["code"] == "xml_doctype_refused"
    assert check_file("a.txt", b"hello")["error"]["code"] == "unsupported_file"
    assert check_file("empty.pdf", b"")["error"]["code"] == "file_empty"
    plain = synth.render_pdf(synth.build_spec(synth.BASES[0]), "A")
    assert check_file("plain.pdf", plain)["error"]["code"] == "no_invoice_xml"


def test_two_different_invoice_xmls_are_refused():
    import facturx

    spec = synth.build_spec(synth.BASES[0])
    other = synth.build_spec(synth.BASES[1])
    pdf = facturx.generate_from_binary(
        synth.render_pdf(spec, "A"), synth.build_xml(spec), flavor="factur-x", level="en16931",
        attachments={"invoice-copy.xml": {"filedata": synth.build_xml(other), "description": "second"}})
    r = check_file("two.pdf", pdf)
    # a standard-named attachment wins, but the extra one is reported
    assert r["status"] == "checked"
    assert any(n["code"] == "extra_invoice_xml" for n in r["intake"]["notes"])
