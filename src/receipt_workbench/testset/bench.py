"""Score the checker on the seeded test set.

For a file with a planted difference there are three outcomes:
  detected      a `mismatch` finding on one of the fields the label names
  abstained     no mismatch reported, but the verdict is `inconclusive` (the tool said it could not tell)
  missed        the tool said "no difference in checked fields"
A mismatch reported only on fields the label does not name counts as `wrong_field`.

A naive baseline is scored on the same files: "flag the invoice if its number, total or
IBAN from the XML does not appear anywhere in the page text".
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from ..common.normalize import squash
from ..pipeline.check import check_file


def _baseline_flags(result: dict, raw_text: str, number_values: set) -> bool:
    from decimal import Decimal

    fields = result["invoice"]["fields"]
    for key in ("invoice_number", "iban"):
        v = fields.get(key)
        if v and squash(v["value"]) not in raw_text:
            return True
    for key in ("grand_total", "due_payable"):
        v = fields.get(key)
        if v and Decimal(v["value"]) not in number_values:
            return True
    return False


def run(testset: Path) -> dict:
    from ..intake.ingest import intake
    from ..page_text.pdftext import read_pdf_text

    manifest = json.loads((testset / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for case in manifest["cases"]:
        data = (testset / case["file"]).read_bytes()
        r = check_file(Path(case["file"]).name, data, run_validation=True)
        comp = r["comparison"]
        mism = [f["key"] for f in comp["findings"] if f["status"] == "mismatch"]
        pdf = read_pdf_text(intake("x.pdf", data).pdf)
        text = squash(" ".join(row.text for row in pdf.rows()))
        numbers = {v for t in pdf.tokens("number") for v in t.values}
        base_flag = _baseline_flags(r, text, numbers)
        if case["kind"] == "clean":
            outcome = "false_alarm" if mism else ("clean_inconclusive" if comp["verdict"] == "inconclusive" else "clean_ok")
        elif set(mism) & set(case["expected"]) or (
                "tax_total" in case["expected"] and any(k.startswith("vat_") for k in mism)):
            outcome = "detected"
        elif mism:
            outcome = "wrong_field"
        elif comp["verdict"] == "inconclusive":
            outcome = "abstained"
        else:
            outcome = "missed"
        rows.append(dict(file=case["file"], base=case["base"], layout=case["layout"], kind=case["kind"],
                         mutation=case["mutation"], side=case["side"], outcome=outcome, verdict=comp["verdict"],
                         mismatch_fields=mism, expected=case["expected"], baseline_flag=base_flag,
                         payment_critical=case.get("payment_critical", False),
                         validation={layer["name"]: layer["outcome"] for layer in r["validation"]},
                         coverage=comp["coverage"]["all"]))
    return summarise(rows, manifest)


def summarise(rows: list[dict], manifest: dict) -> dict:
    clean = [r for r in rows if r["kind"] == "clean"]
    mut = [r for r in rows if r["kind"] == "mutated"]

    def tally(sel):
        c = Counter(r["outcome"] for r in sel)
        n = len(sel)
        return dict(n=n, detected=c["detected"], wrong_field=c["wrong_field"], abstained=c["abstained"], missed=c["missed"],
                    baseline_detected=sum(r["baseline_flag"] for r in sel))

    by_mut, by_layout = defaultdict(list), defaultdict(list)
    for r in mut:
        by_mut[f"{r['mutation']} ({r['side']} changed)"].append(r)
        by_layout[r["layout"]].append(r)
    cov = Counter()
    for r in clean:
        for k, v in r["coverage"].items():
            cov[k] += v
    return dict(
        testset=dict(name=manifest["name"], version=manifest["version"], bases=manifest["bases"], layouts=manifest["layouts"],
                     files=len(rows), clean=len(clean), mutated=len(mut)),
        clean=dict(n=len(clean), false_alarms=sum(r["outcome"] == "false_alarm" for r in clean),
                   inconclusive=sum(r["outcome"] == "clean_inconclusive" for r in clean),
                   baseline_false_alarms=sum(r["baseline_flag"] for r in clean),
                   field_coverage=dict(cov)),
        mutated=tally(mut),
        payment_critical=tally([r for r in mut if r["payment_critical"]]),
        by_mutation={k: tally(v) for k, v in sorted(by_mut.items())},
        by_layout={k: tally(v) for k, v in sorted(by_layout.items())},
        xml_still_rule_valid=sum(r["validation"].get("rules") == "passed" for r in mut),
        rows=rows,
    )


def to_markdown(s: dict) -> str:
    t, c, m = s["testset"], s["clean"], s["mutated"]
    cov = c["field_coverage"]
    out = [
        f"Test set: {t['files']} files from {t['bases']} base invoices x {len(t['layouts'])} layouts "
        f"({t['clean']} clean, {t['mutated']} with one planted difference).",
        "",
        f"- Clean invoices: {c['false_alarms']} false alarms and {c['inconclusive']} inconclusive out of {c['n']} "
        f"(naive baseline: {c['baseline_false_alarms']} false alarms).",
        f"- Planted differences: {m['detected']} of {m['n']} detected on the right field, {m['wrong_field']} flagged on another field, "
        f"{m['abstained']} reported as inconclusive, {m['missed']} missed. Naive baseline: {m['baseline_detected']} of {m['n']}.",
        f"- Clean-file field coverage: {cov.get('match', 0)} of {cov.get('applicable', 0)} fields confirmed at their label, "
        f"{cov.get('present', 0)} found on the page without a label, {cov.get('not_found', 0)} not located.",
        f"- {s['xml_still_rule_valid']} of {m['n']} files with a planted difference still pass schema and rule validation.",
        "",
        "| Planted difference | Files | Detected | Other field | Inconclusive | Missed | Naive baseline |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, v in s["by_mutation"].items():
        out.append(f"| {k} | {v['n']} | {v['detected']} | {v['wrong_field']} | {v['abstained']} | {v['missed']} | {v['baseline_detected']} |")
    out += ["", "| Layout | Files | Detected | Other field | Inconclusive | Missed |", "|---|---|---|---|---|---|"]
    for k, v in s["by_layout"].items():
        out.append(f"| {k} | {v['n']} | {v['detected']} | {v['wrong_field']} | {v['abstained']} | {v['missed']} |")
    return "\n".join(out)


def cmd_bench(args) -> int:
    s = run(Path(args.dir))
    print(to_markdown(s))
    if args.out:
        Path(args.out).write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0
