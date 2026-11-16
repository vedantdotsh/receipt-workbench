"""Command line: check files, survey a folder, build and score the mismatch test set, serve the UI."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .pipeline.check import check_file


def _iter_files(paths: list[str]):
    for p in paths:
        path = Path(p)
        if path.is_dir():
            yield from sorted(x for x in path.rglob("*") if x.suffix.lower() in (".pdf", ".xml"))
        else:
            yield path


def _summary_line(r: dict) -> str:
    name = r["file"]["name"]
    if r["status"] != "checked":
        return f"{r['status']:<12} {name}  [{r.get('error', {}).get('code')}]"
    val = "/".join(f"{layer['name']}:{layer['outcome']}" for layer in r["validation"]) or "validation:skipped"
    c = r["comparison"]
    if not c["applicable"]:
        return f"checked      {name}  {r['invoice']['profile']}  {val}  comparison:{c['reason']}"
    a = c["coverage"]["all"]
    return (f"checked      {name}  {r['invoice']['profile']}  {val}  {c['verdict']}  "
            f"match {a['match']} present {a['present']} mismatch {a['mismatch']} ambiguous {a['ambiguous']} "
            f"not_found {a['not_found']} of {a['applicable']}")


def cmd_check(args) -> int:
    results = []
    for path in _iter_files(args.paths):
        r = check_file(path.name, path.read_bytes(), run_validation=not args.no_validation)
        results.append(r)
        if not args.json:
            print(_summary_line(r))
            if args.verbose and r["status"] == "checked":
                for f in r["comparison"]["findings"]:
                    if args.verbose > 1 or f["status"] not in ("match",):
                        print(f"    {f['status']:<10} {f['key']:<18} xml={f['xml_value']!r} pdf={f['pdf_values']} {f['note'] or ''}")
                for layer in r["validation"]:
                    if layer["detail"]:
                        print(f"    {layer['name']}: {layer['detail']}")
                    for res in layer["results"][:8]:
                        print(f"    {layer['name']}: {res['severity']} {res['rule_id']} {res['message'][:140]}")
    if args.json:
        json.dump(results if len(results) != 1 else results[0], sys.stdout, indent=2, ensure_ascii=False)
    elif len(results) > 1:
        checked = [r for r in results if r["status"] == "checked"]
        compared = [r for r in checked if r["comparison"]["applicable"]]
        print(f"\n{len(results)} files, {len(checked)} checked, {len(compared)} compared")
        print("verdicts:", dict(Counter(r["comparison"]["verdict"] for r in compared)))
        tot = Counter()
        for r in compared:
            for k, v in r["comparison"]["coverage"]["all"].items():
                tot[k] += v
        print("field totals:", dict(tot))
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="receipt-workbench")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="check PDF/XML files or folders")
    c.add_argument("paths", nargs="+")
    c.add_argument("--json", action="store_true")
    c.add_argument("--no-validation", action="store_true")
    c.add_argument("-v", "--verbose", action="count", default=0)
    c.set_defaults(fn=cmd_check)

    g = sub.add_parser("make-testset", help="generate the seeded mismatch test set")
    g.add_argument("--out", default="testset")
    g.set_defaults(fn=lambda a: __import__("receipt_workbench.testset.synth", fromlist=["x"]).cmd_make(a))

    b = sub.add_parser("bench", help="score the checker on a generated test set")
    b.add_argument("--dir", default="testset")
    b.add_argument("--out", default=None)
    b.set_defaults(fn=lambda a: __import__("receipt_workbench.testset.bench", fromlist=["x"]).cmd_bench(a))

    s = sub.add_parser("serve", help="run the review UI locally")
    s.add_argument("--port", type=int, default=8321)
    s.add_argument("--host", default="127.0.0.1")
    s.set_defaults(fn=lambda a: __import__("receipt_workbench.review.server", fromlist=["x"]).cmd_serve(a))

    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
