"""Shared synthetic invoice builders and finding lookups."""

import copy

import pytest
from lxml import etree

from receipt_workbench.testset import synth


def hybrid(base=0, layout="A", mutate=None, side="pdf"):
    spec = synth.build_spec(synth.BASES[base])
    changed = copy.deepcopy(spec)
    if mutate:
        mutate(changed, 1)
    xml_spec, pdf_spec = (changed, spec) if side == "xml" else (spec, changed)
    return synth.make_hybrid(xml_spec, pdf_spec, layout)


def findings(result):
    return {f["key"]: f for f in result["comparison"]["findings"]}


@pytest.fixture(name="hybrid")
def hybrid_factory():
    return hybrid


@pytest.fixture(name="findings")
def findings_factory():
    return findings


@pytest.fixture
def xrechnung_xml():
    root = etree.fromstring(synth.build_xml(synth.build_spec(synth.BASES[0])))
    context = root.find("rsm:ExchangedDocumentContext", synth.NSMAP)
    context.find("ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", synth.NSMAP).text = (
        "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
    )
    process = etree.Element(f"{{{synth.RAM}}}BusinessProcessSpecifiedDocumentContextParameter")
    etree.SubElement(process, f"{{{synth.RAM}}}ID").text = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
    context.insert(0, process)
    agreement = root.find(".//ram:ApplicableHeaderTradeAgreement", synth.NSMAP)
    reference = etree.Element(f"{{{synth.RAM}}}BuyerReference")
    reference.text = "04011000-12345-03"
    agreement.insert(0, reference)
    for party_name in ("SellerTradeParty", "BuyerTradeParty"):
        party = agreement.find(f"ram:{party_name}", synth.NSMAP)
        if party_name == "SellerTradeParty":
            contact = etree.Element(f"{{{synth.RAM}}}DefinedTradeContact")
            etree.SubElement(contact, f"{{{synth.RAM}}}PersonName").text = "Billing office"
            telephone = etree.SubElement(contact, f"{{{synth.RAM}}}TelephoneUniversalCommunication")
            etree.SubElement(telephone, f"{{{synth.RAM}}}CompleteNumber").text = "+49 30 12345678"
            email = etree.SubElement(contact, f"{{{synth.RAM}}}EmailURIUniversalCommunication")
            etree.SubElement(email, f"{{{synth.RAM}}}URIID").text = "billing@example.com"
            party.insert(1, contact)
        address = etree.Element(f"{{{synth.RAM}}}URIUniversalCommunication")
        etree.SubElement(address, f"{{{synth.RAM}}}URIID", schemeID="EM").text = "billing@example.com"
        party.insert(party.index(party.find("ram:PostalTradeAddress", synth.NSMAP)) + 1, address)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")
