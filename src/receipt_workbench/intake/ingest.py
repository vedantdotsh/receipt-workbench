"""Safe intake: hash the original, classify it, pull out the embedded invoice XML.

The file is untrusted. XML is parsed with entity resolution, DTD loading and
network access off, and a DOCTYPE is refused outright. If a PDF carries more than
one plausible invoice XML we report that instead of silently picking one.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field

from lxml import etree

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_XML_BYTES = 5 * 1024 * 1024

KNOWN_XML_NAMES = {
    "factur-x.xml": "Factur-X / ZUGFeRD 2.1+",
    "zugferd-invoice.xml": "ZUGFeRD 1.0 / 2.0",
    "xrechnung.xml": "ZUGFeRD 2.x XRechnung profile",
}

NS_CII = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
NS_ZF1 = "urn:ferd:CrossIndustryDocument:invoice:1p0"
NS_UBL_INV = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_UBL_CN = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"


class IntakeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Attachment:
    name: str
    size: int
    sha256: str
    relationship: str | None
    is_invoice_xml: bool
    data: bytes = field(repr=False, default=b"")


@dataclass
class Intake:
    filename: str
    sha256: str
    size: int
    kind: str  # hybrid_pdf | plain_pdf | xml
    pdf: bytes | None
    xml: bytes | None
    xml_name: str | None
    attachments: list[Attachment]
    notes: list[dict]


def safe_parse_xml(data: bytes) -> etree._Element:
    if len(data) > MAX_XML_BYTES:
        raise IntakeError("xml_too_large", "The XML is larger than the 5 MB limit.")
    head = data[:4096]
    if re.search(rb"<!DOCTYPE|<!ENTITY", head, re.I) or b"<!ENTITY" in data:
        raise IntakeError("xml_doctype_refused", "The XML declares a DOCTYPE or entity. It was not parsed.")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False,
                             huge_tree=False, remove_comments=False, recover=False)
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        raise IntakeError("xml_not_well_formed", f"The XML is not well formed: {e}") from e


def xml_flavor(root: etree._Element) -> str:
    ns = etree.QName(root).namespace
    return {NS_CII: "cii", NS_ZF1: "zugferd1", NS_UBL_INV: "ubl_invoice", NS_UBL_CN: "ubl_creditnote"}.get(ns, "unknown")


def _looks_like_invoice_xml(data: bytes) -> bool:
    head = data[:400_000]  # sample files open with long licence comments
    return any(n.encode() in head for n in (NS_CII, NS_ZF1, NS_UBL_INV, NS_UBL_CN))


def _pdf_attachments(pdf_bytes: bytes) -> list[Attachment]:
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DictionaryObject

    reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    if reader.is_encrypted:
        raise IntakeError("pdf_encrypted", "The PDF is encrypted and was not opened.")
    found: dict[int, Attachment] = {}

    def from_filespec(spec) -> None:
        try:
            spec = spec.get_object()
            if not isinstance(spec, DictionaryObject) or "/EF" not in spec:
                return
            ef = spec["/EF"].get_object()
            stream = (ef.get("/UF") or ef.get("/F")).get_object()
            key = getattr(stream, "idnum", None) or id(stream)
            if key in found:
                return
            data = stream.get_data()
            name = str(spec.get("/UF") or spec.get("/F") or "unnamed")
            rel = spec.get("/AFRelationship")
            found[key] = Attachment(
                name=name, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                relationship=str(rel).lstrip("/") if rel else None,
                is_invoice_xml=_looks_like_invoice_xml(data), data=data,
            )
        except Exception:  # a broken attachment entry must not stop the check
            return

    def walk_names(node, depth=0) -> None:
        if depth > 12:
            return
        node = node.get_object()
        names = node.get("/Names")
        if isinstance(names, ArrayObject):
            for i in range(1, len(names), 2):
                from_filespec(names[i])
        for kid in node.get("/Kids", []) or []:
            walk_names(kid, depth + 1)

    root = reader.trailer["/Root"]
    try:
        if "/Names" in root and "/EmbeddedFiles" in root["/Names"]:
            walk_names(root["/Names"]["/EmbeddedFiles"])
    except Exception:
        pass
    try:
        for spec in root.get("/AF", []) or []:
            from_filespec(spec)
    except Exception:
        pass
    return list(found.values())


def intake(filename: str, data: bytes) -> Intake:
    if len(data) > MAX_FILE_BYTES:
        raise IntakeError("file_too_large", "The file is larger than the 15 MB limit.")
    if not data:
        raise IntakeError("file_empty", "The file is empty.")
    sha = hashlib.sha256(data).hexdigest()
    notes: list[dict] = []
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")

    if data[:1024].find(b"%PDF-") != -1:
        try:
            atts = _pdf_attachments(data)
        except IntakeError:
            raise
        except Exception as e:
            raise IntakeError("pdf_unreadable", f"The PDF could not be read: {e}") from e
        candidates = [a for a in atts if a.is_invoice_xml]
        if not candidates:
            return Intake(filename, sha, len(data), "plain_pdf", data, None, None, atts, notes)
        distinct = {a.sha256 for a in candidates}
        if len(distinct) > 1:
            named = [a for a in candidates if a.name.lower() in KNOWN_XML_NAMES]
            if len({a.sha256 for a in named}) != 1:
                raise IntakeError(
                    "ambiguous_attachment",
                    "The PDF contains more than one different invoice XML ("
                    + ", ".join(sorted(a.name for a in candidates))
                    + "). Nothing was checked, because software may read a different one than this tool.",
                )
            notes.append({
                "code": "extra_invoice_xml",
                "detail": "Other invoice-like XML attachments exist besides the standard-named one: "
                + ", ".join(sorted(a.name for a in candidates if a not in named)),
            })
            chosen = named[0]
        else:
            chosen = candidates[0]
        if chosen.name.lower() not in KNOWN_XML_NAMES:
            notes.append({"code": "nonstandard_attachment_name",
                          "detail": f"The invoice XML is attached as '{chosen.name}', not a standard file name."})
        return Intake(filename, sha, len(data), "hybrid_pdf", data, chosen.data, chosen.name, atts, notes)

    if stripped[:1] == b"<":
        return Intake(filename, sha, len(data), "xml", None, data, filename, [], notes)

    raise IntakeError("unsupported_file", "Only PDF and XML files are supported.")
