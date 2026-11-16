import copy
import json
from importlib import metadata
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from receipt_workbench.cli import main
from receipt_workbench.harness import Export, Registry
from receipt_workbench.harness.registry import BUILTINS, POINTS
from receipt_workbench.invoice_data.cii import Invoice
from receipt_workbench.page_text.pdftext import PdfText, read_pdf_text
from receipt_workbench.pipeline.check import check_file, needs_review
from receipt_workbench.review import server
from receipt_workbench.review.limits import Settings
from receipt_workbench.testset import synth
from receipt_workbench.validation.validate import Layer

SOURCE = {"distribution": "example-checks", "version": "1.2.3"}


def passed(xml, flavor, profile, guideline):
    return Layer("ignored-name", "passed", "example")


def test_builtins_are_entry_points():
    registry = Registry()
    for point in POINTS:
        entries = metadata.entry_points(group=f"receipt_workbench.{point}")
        assert {entry.name for entry in entries} == set(BUILTINS[point])
        assert [part.name for part in registry.active(point)] == list(BUILTINS[point])
        assert all(part.distribution == "receipt-workbench" and part.version == "0.1.0"
                   for part in registry.active(point))


def test_extra_validator_in_record_cli_and_api(hybrid, monkeypatch, capsys, tmp_path):
    registry = Registry()
    registry.register("validators", "extra", passed, **SOURCE)
    monkeypatch.setattr("receipt_workbench.cli.get_registry", lambda: registry)
    monkeypatch.setattr("receipt_workbench.pipeline.check.get_registry", lambda: registry)
    data = hybrid()
    record = check_file("invoice.pdf", data, registry=registry)
    provenance = {"name": "extra", **SOURCE}
    assert record["tool"]["record_version"] == 2
    assert record["parts"]["validators"][-1] == provenance
    assert record["validation"][-1]["part"] == provenance
    assert record["validation"][-1]["name"] == "extra"
    assert main(["parts"]) == 0
    assert "extra  example-checks  1.2.3  active" in capsys.readouterr().out
    path = tmp_path / "invoice.pdf"
    path.write_bytes(data)
    assert main(["check", str(path)]) == 0
    assert "extra:passed" in capsys.readouterr().out
    client = TestClient(server.create_app(Settings(), registry=registry))
    uploaded = client.post("/api/check", files={"files": ("invoice.pdf", data)}).json()[0]
    item = client.get(f"/api/items/{uploaded['id']}").json()
    assert uploaded["validation"]["extra"] == "passed"
    assert item["result"]["validation"][-1]["part"] == provenance
    assert client.get("/api/harness").json()["parts"] == registry.describe()
    bundle = client.get(f"/api/items/{uploaded['id']}/evidence").json()
    assert bundle["result"]["parts"]["exports"] == [
        {"name": "json", "distribution": "receipt-workbench", "version": "0.1.0"}]
    assert client.get(f"/api/items/{uploaded['id']}").json()["result"]["parts"]["exports"] == []


@pytest.mark.parametrize("outcome", ["passed", "failed", "error"])
def test_validator_never_clears_a_difference(outcome, hybrid):
    registry = Registry()
    registry.register("validators", "extra", lambda *args: Layer("schema", outcome, "example"), **SOURCE)
    result = check_file("changed.pdf", hybrid(mutate=synth.m_iban), registry=registry)
    assert result["comparison"]["verdict"] == "difference_found"
    assert result["validation"][0]["name"] == "schema"
    assert result["validation"][-1]["name"] == "extra"
    assert needs_review(result)


def test_raising_validator_is_error(hybrid):
    def broken(*args):
        raise RuntimeError("cannot validate")

    registry = Registry()
    registry.register("validators", "broken", broken, **SOURCE)
    result = check_file("clean.pdf", hybrid(), registry=registry)
    assert result["status"] == "checked"
    assert result["validation"][-1]["outcome"] == "error"
    assert result["validation"][-1]["detail"] == "RuntimeError: cannot validate"
    assert result["parts"]["validators"][-1] == {"name": "broken", **SOURCE}
    assert needs_review(result)


def test_invalid_validator_result_is_error(hybrid):
    registry = Registry()
    registry.register("validators", "invalid", lambda *args: Layer("invalid", "unknown", "example"), **SOURCE)
    result = check_file("clean.pdf", hybrid(), registry=registry)
    assert result["validation"][-1]["outcome"] == "error"
    assert needs_review(result)


def test_broken_entry_point_reported_and_skipped(monkeypatch, caplog, capsys):
    def broken():
        raise ImportError("missing package")

    original = metadata.entry_points
    entry = SimpleNamespace(name="broken", dist=SimpleNamespace(metadata={"Name": "example-checks"}, version="1.2.3"),
                            load=broken)
    monkeypatch.setattr(metadata, "entry_points", lambda *, group: [*original(group=group), entry]
                        if group.endswith(".validators") else original(group=group))
    registry = Registry()
    assert [part.name for part in registry.active("validators")] == ["schema", "rules", "xrechnung"]
    assert registry.describe()["validators"][-1] == {
        "name": "broken", **SOURCE, "active": False, "error": "ImportError: missing package"}
    assert "broken" in caplog.text and "example-checks" in caplog.text and "missing package" in caplog.text
    monkeypatch.setattr("receipt_workbench.cli.get_registry", lambda: registry)
    main(["parts"])
    assert "broken  example-checks  1.2.3  load error: ImportError" in capsys.readouterr().out


@pytest.mark.parametrize("setting,value,expected", [
    ("RW_DISABLE_VALIDATORS", "rules", ["schema", "xrechnung"]),
    ("RW_VALIDATORS", "rules", ["rules"]),
    ("RW_VALIDATORS", "", []),
])
def test_selection(monkeypatch, setting, value, expected, hybrid):
    monkeypatch.setenv(setting, value)
    registry = Registry()
    assert [part.name for part in registry.active("validators")] == expected
    result = check_file("clean.pdf", hybrid(), registry=registry)
    assert [layer["name"] for layer in result["validation"]] == expected
    assert all(part["active"] == (part["name"] in expected) for part in registry.describe()["validators"])


def test_extensions_sorted_after_builtins():
    registry = Registry()
    for name in ("z-last", "a-first"):
        registry.register("validators", name, passed, **SOURCE)
    assert [part.name for part in registry.active("validators")] == ["schema", "rules", "xrechnung", "a-first", "z-last"]
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register("validators", "schema", passed, **SOURCE)


def test_second_parser_and_replacement_reader(monkeypatch, hybrid):
    class UblParser:
        flavors = ("ubl_invoice",)

        def __call__(self, root):
            assert root.tag.endswith("Invoice")
            return Invoice("ubl_invoice", "EXAMPLE", "example")

    seen = []

    def reader(data):
        seen.append(data)
        return PdfText([])

    monkeypatch.setenv("RW_PAGE_READERS", "scan")
    registry = Registry()
    registry.register("invoice_parsers", "ubl", UblParser(), **SOURCE)
    registry.register("page_readers", "scan", reader, **SOURCE)
    xml = b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
    parsed = check_file("ubl.xml", xml, registry=registry)
    assert parsed["status"] == "checked" and parsed["invoice"]["profile"] == "EXAMPLE"
    assert parsed["parts"]["invoice_parsers"] == [{"name": "ubl", **SOURCE}]
    data = hybrid()
    result = check_file("scan.pdf", data, registry=registry)
    assert seen == [data]
    assert result["comparison"]["reason"] == "no_text_layer"
    assert result["parts"]["page_readers"] == [{"name": "scan", **SOURCE}]


def test_extra_export_by_name(hybrid):
    def export(result, events):
        assert result["parts"]["exports"] == [{"name": "text", **SOURCE}]
        assert events[0]["decision"] == "reviewed"
        result["status"] = "changed"
        events.clear()
        return Export(b"handoff", "text/plain", "invoice.txt")

    registry = Registry()
    registry.register("exports", "text", export, **SOURCE)
    application = server.create_app(Settings(), registry=registry)
    client = TestClient(application)
    item_id = client.post("/api/check", files={"files": ("invoice.pdf", hybrid())}).json()[0]["id"]
    url = f"/api/items/{item_id}"
    client.post(url + "/review", json={"decision": "reviewed"})
    response = client.get(url + "/exports/text")
    assert response.content == b"handoff" and response.headers["content-type"].startswith("text/plain")
    assert "invoice.txt" in response.headers["content-disposition"]
    assert client.get(url).json()["result"]["status"] == "checked"
    assert client.get(url).json()["events"]
    assert client.get(url + "/exports/absent").status_code == 404
    assert TestClient(application).get(url + "/exports/text").status_code == 404


def test_hosting_notice_and_override(monkeypatch):
    local = TestClient(server.create_app(Settings())).get("/api/harness").json()["hosting_notice"]
    public_settings = Settings.from_env(public=True)
    public = TestClient(server.create_app(public_settings)).get("/api/harness").json()["hosting_notice"]
    assert "this computer" in local["en"] and "diesem Rechner" in local["de"]
    assert "this server" in public["en"] and "diesen Server" in public["de"]
    assert "f\u00fcr diese Browsersitzung" in public["de"] and "Aktivit\u00e4t" in public["de"]
    assert "30 minutes" in public["en"] and "30 Minuten" in public["de"]
    assert "47" in Settings(public=True, idle_seconds=47).hosting_notice()["en"]
    assert "disabled" in Settings(public=True).hosting_notice()["en"]
    monkeypatch.setenv("RW_HOSTING_NOTICE_EN", 'English <notice> "quoted"')
    monkeypatch.setenv("RW_HOSTING_NOTICE_DE", "Deutscher Hinweis")
    client = TestClient(server.create_app(public_settings))
    notices = client.get("/api/harness").json()["hosting_notice"]
    assert notices == {"en": 'English <notice> "quoted"', "de": "Deutscher Hinweis"}
    page = client.get("/").text
    assert 'data-hosting-en="English &lt;notice&gt; &quot;quoted&quot;"' in page


def test_upload_does_not_spool_to_disk(monkeypatch):
    import starlette.formparsers

    original = starlette.formparsers.SpooledTemporaryFile
    files = []

    def spool(*args, **kwargs):
        file = original(*args, **kwargs)
        files.append(file)
        return file

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", spool)
    client = TestClient(server.create_app(Settings.from_env(public=True)))
    assert client.post("/api/check", files={"files": ("large.xml", b"x" * (2 * 1024**2))}).status_code == 200
    assert files and all(not file._rolled and file.closed for file in files)


def test_builtin_record_content_unchanged(hybrid):
    from receipt_workbench.validation.validate import (
        validate_rules,
        validate_schema,
        validate_xrechnung,
    )

    data = hybrid()
    result = check_file("clean.pdf", data, registry=Registry())
    xml = server.intake("clean.pdf", data).xml
    invoice = result["invoice"]
    expected = [validate_schema(xml, "cii", invoice["profile"]),
                validate_rules(xml, "cii", invoice["profile"], invoice["guideline"]),
                validate_xrechnung(xml, "cii", invoice["guideline"])]
    layers = copy.deepcopy(result["validation"])
    for layer in layers:
        layer.pop("part")
    assert layers == [layer.to_dict() for layer in expected]
    assert read_pdf_text(data).has_text
    assert json.loads(json.dumps(result))["comparison"]["verdict"] == "no_difference_in_checked_fields"
