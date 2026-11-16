from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

TOOLS = {
    "mustang": {"name": "Mustangproject", "version": "2.26.0", "jar": "Mustang-CLI-2.26.0.jar",
                    "sha256": "42d7868cb68264874a7b8cab4c3587b03b23ccc7cd72373da917f66758bb9736"},
    "verapdf": {"name": "veraPDF", "version": "1.30.4", "jar": "verapdf/bin/cli-1.30.4.jar",
                    "sha256": "61f833f6766ca277784fe70b2048161b2ca65ee0de2f3ba092b822ba883c63ac"},
}
DOWNLOADS = [
    {"file": "Mustang-CLI-2.26.0.jar", "license": "Apache-2.0",
         "url": "https://github.com/ZUGFeRD/mustangproject/releases/download/core-2.26.0/Mustang-CLI-2.26.0.jar",
         "sha256": TOOLS["mustang"]["sha256"]},
    {"file": "verapdf-greenfield-1.30.4-installer.zip", "license": "GPL-3.0-or-later OR MPL-2.0",
         "url": "https://software.verapdf.org/rel/1.30/verapdf-greenfield-1.30.4-installer.zip",
         "sha256": "04415ef3f149526a3048b9889723e81320ddf013bd891bcc7f0f297f3eadcc23"},
    {"file": "OpenJDK21U-jre_x64_windows_hotspot_21.0.13_9.zip", "license": "GPL-2.0 WITH Classpath-exception-2.0",
         "url": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/"
             "OpenJDK21U-jre_x64_windows_hotspot_21.0.13_9.zip",
         "sha256": "2332c0b4cff6bed722da3567c91d5e47568bcba863fa4945d0a8d6fc76158628"},
]
FIELD_TERMS = {
    "invoice_number": "1", "issue_date": "2", "due_date": "9", "seller_vat_id": "31",
    "iban": "84", "bic": "86", "line_total": "106", "tax_basis_total": "109",
    "tax_total": "110", "grand_total": "112", "due_payable": "115",
}
FIELD_ELEMENTS = {
    "iban": "IBANID", "bic": "BICID", "issue_date": "IssueDateTime", "due_date": "DueDateDateTime",
    "line_total": "LineTotalAmount", "tax_basis_total": "TaxBasisTotalAmount", "tax_total": "TaxTotalAmount",
    "grand_total": "GrandTotalAmount", "due_payable": "DuePayableAmount",
}


def _root(text: str, tag: str) -> ET.Element:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DOCTYPE/entity in report")
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError as exc:
        raise ValueError(f"malformed XML report: {exc}") from exc
    if root.tag != tag:
        raise ValueError(f"expected {tag} report, got {root.tag}")
    return root


def _fields(message: str, location: str, criterion: str) -> list[str]:
    fields = {key for key, term in FIELD_TERMS.items() if re.search(rf"\bBT-{term}\b", message)}
    path = location + " " + criterion
    fields.update(key for key, element in FIELD_ELEMENTS.items() if re.search(rf"\b{element}\b", path))
    line_path = re.sub(r"\[namespace-uri\(\)='[^']*'\]", "", location)
    for index in re.findall(r"IncludedSupplyChainTradeLineItem\[(\d+)\]", line_path):
        fields.add(f"line_{index}")
    return sorted(fields)


def parse_mustang(text: str) -> dict:
    root = _root(text, "validation")
    summary = root.find("summary")
    validator = root.find("./xml/info/validator")
    if summary is None or summary.get("status") not in ("valid", "invalid") or validator is None:
        raise ValueError("missing Mustang verdict/version")
    layers, issues = {}, []
    for scope in ("pdf", "xml"):
        layer = root.find(f"{scope}/summary")
        if layer is not None:
            if layer.get("status") not in ("valid", "invalid"):
                raise ValueError(f"unexpected {scope} status")
            layers[scope] = layer.get("status")
    for scope, parent in (("document", root), ("xml", root.find("xml")), ("pdf", root.find("pdf"))):
        if parent is None:
            continue
        for node in parent.findall("messages/*"):
            message = "".join(node.itertext()).strip()
            rule = re.search(r"\[ID ([^\]]+)\]", message)
            location, criterion = node.get("location", ""), node.get("criterion", "")
            issues.append({"id": rule.group(1) if rule else f"mustang:{node.get('type', node.tag)}",
                               "message_id": node.get("type"), "scope": scope, "severity": node.tag, "message": message,
                               "location": location, "criterion": criterion, "fields": _fields(message, location, criterion)})
    pdf = root.find("pdf")
    if pdf is not None:
        pattern = (r"TestAssertion \[ruleId=RuleId \[specification=(.*?), clause=(.*?), testNumber=(\d+)\], "
                   r"status=failed, message=(.*?), location=")
        seen = set()
        for specification, clause, number, message in re.findall(pattern, pdf.text or "", re.DOTALL):
            rule_id = f"{specification}:{clause}:{number}"
            if rule_id not in seen:
                issues.append({"id": rule_id, "message_id": None, "scope": "pdf", "severity": "error", "message": message,
                                   "location": "", "criterion": "", "fields": []})
                seen.add(rule_id)
        if layers.get("pdf") == "invalid" and not seen:
            raise ValueError("unrecognised Mustang PDF diagnostics")
    return {"verdict": summary.get("status"), "version": validator.get("version"), "layers": layers, "issues": issues}


def parse_verapdf(text: str) -> dict:
    root = _root(text, "report")
    build = root.find("./buildInformation/releaseDetails[@id='apps']")
    batch = root.find("batchSummary")
    jobs = root.findall("./jobs/job")
    if build is None or batch is None or batch.get("totalJobs") != "1" or len(jobs) != 1:
        raise ValueError("missing veraPDF job/version")
    if any(batch.get(key, "0") != "0" for key in ("failedToParse", "encrypted", "outOfMemory", "veraExceptions")):
        raise ValueError("veraPDF could not validate the file")
    report = jobs[0].find("validationReport")
    if report is None or report.get("jobEndStatus") != "normal" or report.get("isCompliant") not in ("true", "false"):
        raise ValueError("missing or unsuccessful veraPDF validation")
    issues = []
    for rule in report.findall("./details/rule[@status='failed']"):
        rule_id = ":".join(rule.attrib[key] for key in ("specification", "clause", "testNumber"))
        issues.append({"id": rule_id, "message_id": None, "scope": "pdf", "severity": "error",
                           "message": rule.findtext("description", ""), "location": "", "criterion": rule.findtext("test", ""),
                           "fields": [], "failed_checks": int(rule.get("failedChecks", "0")),
                           "error_message": rule.findtext("./check/errorMessage", "")})
    valid = report.get("isCompliant") == "true"
    if valid == bool(issues):
        raise ValueError("veraPDF verdict contradicts failed rules")
    return {"verdict": "valid" if valid else "invalid", "version": build.get("version"), "layers": {}, "issues": issues}


def _issue_key(issue: dict) -> tuple:
    return tuple(issue[key] for key in ("scope", "id", "location", "criterion"))


def classify(case: dict, report: dict, baseline: dict) -> str:
    if report["verdict"] == "error":
        return "error"
    if case["kind"] == "clean":
        return "accepted" if report["verdict"] == "valid" else "rejected"
    if report["verdict"] == "valid":
        return "not_flagged"
    if baseline["verdict"] == "error":
        return "error"
    previous = {_issue_key(issue) for issue in baseline["issues"]}
    related = any(issue["severity"] in ("error", "fatal") and issue["scope"] == case["side"]
                  and _issue_key(issue) not in previous and set(issue["fields"]) & set(case["expected"])
                  for issue in report["issues"])
    return "related" if related else "unrelated"


def invoke(command: list[str], tool: str, report_path: Path, timeout: float) -> dict:
    start = time.perf_counter()
    result = {"verdict": "error", "version": TOOLS[tool]["version"], "layers": {}, "issues": [], "exit_status": None, "error": None}
    stdout, stderr = b"", b""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                                 cwd=report_path.parent, check=False)
        stdout, stderr = process.stdout, process.stderr
        result["exit_status"] = process.returncode
        parsed = (parse_mustang if tool == "mustang" else parse_verapdf)(stdout.decode("utf-8"))
        result.update(parsed)
        allowed = (0, -1, 4294967295) if tool == "mustang" else (0, 1)
        if parsed["version"] != TOOLS[tool]["version"]:
            raise ValueError(f"unexpected version {parsed['version']}")
        if process.returncode not in allowed or (parsed["verdict"] == "valid" and process.returncode != 0):
            raise ValueError(f"unexpected exit status {process.returncode}")
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
        result.update(verdict="error", error=f"timeout after {timeout:g}s")
    except (OSError, ValueError, KeyError, ET.ParseError) as exc:
        result.update(verdict="error", error=str(exc))
    report_path.write_bytes(stdout)
    report_path.with_suffix(".stderr.txt").write_bytes(stderr)
    result.update(elapsed_seconds=round(time.perf_counter() - start, 3), report_sha256=hashlib.sha256(stdout).hexdigest(),
                  report_file=str(report_path), stderr=stderr.decode("utf-8", errors="replace"))
    return result


def tool_command(tool: str, cache: Path, java: Path) -> list[str]:
    command = [str(java.resolve()), "-Xmx512m", "-XX:TieredStopAtLevel=1", "-Dfile.encoding=UTF-8",
               "-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8", "-jar", str((cache / TOOLS[tool]["jar"]).resolve())]
    return command + (["--action", "validate", "--disable-file-logging", "--source"] if tool == "mustang"
                      else ["--format", "xml", "--flavour", "3b"])


def provenance(cache: Path, java: Path, tools: list[str]) -> dict:
    binaries = []
    for tool in tools:
        metadata = TOOLS[tool]
        path = cache / metadata["jar"]
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != metadata["sha256"]:
            raise ValueError(f"SHA-256 mismatch: {path}")
        binaries.append(dict(metadata, sha256=digest))
    runtime = subprocess.run([str(java.resolve()), "-version"], capture_output=True, timeout=15, check=True)
    return {"downloads": DOWNLOADS, "binaries": binaries, "java": (runtime.stdout + runtime.stderr).decode("utf-8"),
                "platform": os.name}


def run(testset: Path, cache: Path, java: Path, tools: list[str], timeout: float, jobs: int,
        clean_only: bool = False, reports: Path | None = None) -> dict:
    if timeout <= 0 or jobs < 1:
        raise ValueError("timeout and jobs must be positive")
    start = time.perf_counter()
    manifest_bytes = (testset / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    metadata = provenance(cache, java, tools)
    clean = [case for case in manifest["cases"] if case["kind"] == "clean"]
    changed = [] if clean_only else [case for case in manifest["cases"] if case["kind"] == "mutated"]
    if len(clean) + len(changed) != len(manifest["cases"]) and not clean_only:
        raise ValueError("unknown manifest case kind")
    summaries = []
    for tool in tools:
        tool_start = time.perf_counter()
        command = tool_command(tool, cache, java)

        def check(case: dict, tool: str = tool, command: list[str] = command) -> dict:
            path = (testset / case["file"]).resolve()
            result = {"verdict": "error", "version": TOOLS[tool]["version"], "layers": {}, "issues": [], "exit_status": None,
                          "error": "file missing, outside test set, or SHA-256 differs from manifest", "elapsed_seconds": 0}
            if (path.is_relative_to(testset.resolve()) and path.is_file()
                    and hashlib.sha256(path.read_bytes()).hexdigest() == case["sha256"]):
                report_path = (reports or cache / "reports").resolve() / tool / Path(case["file"]).with_suffix(".xml")
                result = invoke(command + [str(path)], tool, report_path, timeout)
            return dict(case, tool=TOOLS[tool]["name"], **result)

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            clean_rows = list(pool.map(check, clean))
            baselines = {(row["base"], row["layout"]): row for row in clean_rows}
            if any((case["base"], case["layout"]) not in baselines for case in changed):
                raise ValueError("changed case has no paired clean file")
            rows = clean_rows + list(pool.map(check, changed))
        for row in rows:
            row["classification"] = classify(row, row, baselines[(row["base"], row["layout"])])
        counts = Counter(row["classification"] for row in rows)
        summaries.append({"name": TOOLS[tool]["name"], "version": TOOLS[tool]["version"], "command": command,
                              "elapsed_seconds": round(time.perf_counter() - tool_start, 3),
                              "clean": {"n": len(clean), "accepted": counts["accepted"], "rejected": counts["rejected"],
                                         "errors": sum(row["classification"] == "error" for row in rows[:len(clean)])},
                              "changed": {"n": len(changed), "related": counts["related"], "unrelated": counts["unrelated"],
                                           "not_flagged": counts["not_flagged"],
                                           "errors": sum(row["classification"] == "error" for row in rows[len(clean):])},
                              "rows": rows})
    return {"testset": manifest["name"], "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "date": datetime.now(UTC).isoformat(), "timeout_seconds": timeout, "jobs": jobs, "provenance": metadata,
                "elapsed_seconds": round(time.perf_counter() - start, 3), "tools": summaries}


def cmd_third_party(args) -> int:
    cache = Path(args.cache)
    java = Path(args.java) if args.java else cache / "java/jdk-21.0.13+9-jre/bin/java.exe"
    result = run(Path(args.dir), cache, java, args.tool or list(TOOLS), args.timeout, args.jobs, args.clean_only,
                 Path(args.out).resolve().parent / "reports")
    Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for tool in result["tools"]:
        print(f"{tool['name']} {tool['version']}: clean {tool['clean']}; changed {tool['changed']}; "
              f"{tool['elapsed_seconds']}s")
    return int(any(tool["clean"]["errors"] or tool["changed"]["errors"] for tool in result["tools"]))
