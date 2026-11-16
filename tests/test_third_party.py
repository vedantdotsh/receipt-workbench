import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from receipt_workbench.testset.third_party import (
    _fields,
    classify,
    invoke,
    parse_mustang,
    parse_verapdf,
    provenance,
    run,
)

SAMPLES = Path(__file__).parent / "fixtures" / "third_party"


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def test_mustang_overall_verdict_is_not_pdf_verdict():
    report = parse_mustang(sample("mustang-valid.xml"))
    assert report["verdict"] == "valid"
    assert report["version"] == "2.26.0"
    assert report["layers"] == {"pdf": "invalid", "xml": "valid"}
    assert {issue["id"] for issue in report["issues"] if issue["scope"] == "pdf"} == {
        "ISO 19005-3:2012:6.2.4.3:4", "ISO 19005-3:2012:6.2.11.4.1:1",
    }
    assert "BR-DE-15" in {issue["id"] for issue in report["issues"]}
    assert any("übermittelt" in issue["message"] for issue in report["issues"])
    assert all(issue["severity"] == "notice" for issue in report["issues"] if issue["scope"] == "xml")


def test_mustang_invalid_records_rule_and_message_ids():
    report = parse_mustang(sample("mustang-invalid.xml"))
    assert report["verdict"] == "invalid"
    errors = [issue for issue in report["issues"] if issue["severity"] == "error"]
    assert {issue["id"] for issue in errors} == {"BR-CO-15", "BR-CO-16"}
    assert all(issue["message_id"] == "4" for issue in errors)
    assert "grand_total" in errors[0]["fields"]


def test_verapdf_invalid_is_not_a_process_error():
    report = parse_verapdf(sample("verapdf-invalid.xml"))
    assert report["version"] == "1.30.4"
    assert report["verdict"] == "invalid"
    assert {issue["id"] for issue in report["issues"]} == {
        "ISO 19005-3:2012:6.2.4.3:4", "ISO 19005-3:2012:6.2.11.4.1:1",
    }
    assert all(not issue["fields"] for issue in report["issues"])


@pytest.mark.parametrize("parser,name", [
    (parse_mustang, "mustang-valid.xml"), (parse_verapdf, "verapdf-invalid.xml"),
])
@pytest.mark.parametrize("damage", ["truncated", "wrong-root", "doctype"])
def test_report_parsers_reject_unusable_output(parser, name, damage):
    text = sample(name)
    text = {"truncated": text[:80], "wrong-root": "<unknown/>",
            "doctype": '<!DOCTYPE report [<!ENTITY value "x">]>' + text}[damage]
    with pytest.raises(ValueError):
        parser(text)


def test_verapdf_exception_does_not_become_invalid():
    text = sample("verapdf-invalid.xml").replace('veraExceptions="0"', 'veraExceptions="1"')
    with pytest.raises(ValueError):
        parse_verapdf(text)


def test_valid_verapdf_report():
    text = sample("verapdf-invalid.xml").replace('isCompliant="false"', 'isCompliant="true"')
    with pytest.raises(ValueError):
        parse_verapdf(text)
    root = ET.fromstring(text)
    details = root.find("./jobs/job/validationReport/details")
    for rule in list(details):
        details.remove(rule)
    details.set("failedRules", "0")
    assert parse_verapdf(ET.tostring(root, encoding="unicode"))["verdict"] == "valid"


def test_related_rule_requires_new_blocking_field_evidence():
    baseline = parse_mustang(sample("mustang-valid.xml"))
    report = parse_mustang(sample("mustang-invalid.xml"))
    case = {"kind": "mutated", "side": "xml", "expected": ["grand_total"]}
    assert classify(case, report, baseline) == "related"
    assert classify(dict(case, side="pdf"), report, baseline) == "unrelated"
    assert classify(dict(case, expected=["iban"]), report, baseline) == "unrelated"
    assert classify(case, report, report) == "unrelated"
    assert classify(case, baseline, baseline) == "not_flagged"
    assert classify(case, dict(report, verdict="error"), baseline) == "error"
    assert classify(case, report, dict(baseline, verdict="error")) == "error"
    assert classify(dict(case, kind="clean"), baseline, baseline) == "accepted"
    assert classify(dict(case, kind="clean"), report, baseline) == "rejected"
    notices = dict(report, issues=[dict(issue, severity="notice") for issue in report["issues"]])
    assert classify(case, notices, baseline) == "unrelated"


def test_field_mapping_distinguishes_business_terms_and_line_positions():
    assert _fields("Buyer reference (BT-10)", "", "") == []
    path = "/*:IncludedSupplyChainTradeLineItem[namespace-uri()='urn:example:100'][3]/ram:LineTotalAmount"
    assert _fields("", path, "") == ["line_3", "line_total"]


@pytest.mark.parametrize("text", ["not XML", '<validation><summary status="valid"/></validation>'])
def test_invocation_unusable_report_is_an_error(monkeypatch, tmp_path, text):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 0, text.encode(), b"diagnostic"))
    result = invoke(["java"], "mustang", tmp_path / "report.xml", 2)
    assert result["verdict"] == "error"
    assert result["exit_status"] == 0
    assert result["stderr"] == "diagnostic"


def test_verapdf_noncompliance_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 1, sample("verapdf-invalid.xml").encode(), b""))
    assert invoke(["java"], "verapdf", tmp_path / "report.xml", 2)["verdict"] == "invalid"


def test_binary_hash_is_checked_before_executing_java(tmp_path):
    (tmp_path / "Mustang-CLI-2.26.0.jar").write_bytes(b"not the pinned binary")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        provenance(tmp_path, Path("not-installed-java"), ["mustang"])


@pytest.mark.parametrize("exit_status,verdict", [(0, "valid"), (7, "error"), (-9, "error")])
def test_invocation_checks_exit_status(monkeypatch, tmp_path, exit_status, verdict):
    def execute(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], exit_status, sample("mustang-valid.xml").encode(), b"")

    monkeypatch.setattr(subprocess, "run", execute)
    result = invoke(["java"], "mustang", tmp_path / "report.xml", 2)
    assert result["verdict"] == verdict
    assert result["exit_status"] == exit_status


@pytest.mark.parametrize("exit_status", [-1, 4294967295])
def test_mustang_invalid_exit_status(monkeypatch, tmp_path, exit_status):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], exit_status, sample("mustang-invalid.xml").encode(), b""))
    assert invoke(["java"], "mustang", tmp_path / "report.xml", 2)["verdict"] == "invalid"


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired(["java"], 2), OSError("not executable")])
def test_invocation_failure_is_an_error(monkeypatch, tmp_path, failure):
    def execute(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", execute)
    result = invoke(["java"], "mustang", tmp_path / "report.xml", 2)
    assert result["verdict"] == "error"
    assert result["exit_status"] is None
    assert result["error"]


def test_runner_checks_clean_first_and_manifest_hashes(monkeypatch, tmp_path):
    import hashlib

    from receipt_workbench.testset import third_party

    cases = []
    for kind in ("mutated", "clean"):
        path = tmp_path / f"{kind}.pdf"
        path.write_bytes(b"invoice")
        cases.append({"file": path.name, "base": "b01", "layout": "A", "kind": kind, "mutation": "gross_total",
                          "side": "xml", "expected": ["grand_total"], "sha256": hashlib.sha256(b"invoice").hexdigest()})
    (tmp_path / "manifest.json").write_text(json.dumps({"name": "test", "version": 1, "cases": cases}))
    monkeypatch.setattr(third_party, "tool_command", lambda *args: ["java"])
    monkeypatch.setattr(third_party, "provenance", lambda *args: {})
    seen = []

    def execute(command, tool, report_path, timeout):
        assert report_path.is_relative_to(tmp_path / "reports")
        seen.append(report_path.name)
        return dict(parse_mustang(sample("mustang-valid.xml")), exit_status=0, elapsed_seconds=0.1)

    monkeypatch.setattr(third_party, "invoke", execute)
    report = run(tmp_path, tmp_path / "cache", Path("java"), ["mustang"], 2, 1, reports=tmp_path / "reports")
    assert seen == ["clean.xml", "mutated.xml"]
    assert report["tools"][0]["clean"]["accepted"] == 1
    assert report["tools"][0]["changed"]["not_flagged"] == 1
    seen.clear()
    (tmp_path / "mutated.pdf").write_bytes(b"changed")
    report = run(tmp_path, tmp_path / "cache", Path("java"), ["mustang"], 2, 1, reports=tmp_path / "reports")
    assert seen == ["clean.xml"]
    assert report["tools"][0]["changed"]["errors"] == 1
