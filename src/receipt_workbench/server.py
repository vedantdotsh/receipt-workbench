"""Local review UI. Single user, files kept in memory only, nothing leaves the machine."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from .check import check_file, needs_review
from .ingest import MAX_FILE_BYTES, intake
from .pdftext import render_page_png

STATIC = Path(__file__).parent / "static"
MAX_ITEMS = 200
DECISIONS = {"reviewed", "needs_clarification"}

app = FastAPI(title="Receipt Workbench", docs_url=None, redoc_url=None)
ITEMS: dict[str, dict] = {}  # id -> {"result", "pdf", "events", "pages"}


def _summary(item_id: str, item: dict) -> dict:
    r = item["result"]
    inv = r.get("invoice") or {}
    fields = inv.get("fields") or {}
    comp = r.get("comparison") or {}

    def fv(k):
        return (fields.get(k) or {}).get("value")

    return {
        "id": item_id, "file": r["file"]["name"], "status": r["status"], "error": r.get("error"),
        "invoice_number": fv("invoice_number"), "seller": fv("seller_name"), "currency": fv("currency"),
        "amount_due": fv("due_payable") or fv("grand_total"), "profile": inv.get("profile"),
        "validation": {layer["name"]: layer["outcome"] for layer in r.get("validation", [])},
        "verdict": comp.get("verdict"), "comparison_reason": comp.get("reason"),
        "coverage": (comp.get("coverage") or {}).get("all"),
        "needs_review": needs_review(r), "sample": item.get("sample"),
        "decision": item["events"][-1]["decision"] if item["events"] else None,
    }


def _add(filename: str, data: bytes, sample: str | None = None) -> dict:
    if len(ITEMS) >= MAX_ITEMS:
        raise HTTPException(429, "Inbox is full. Clear it first.")
    result = check_file(filename, data)
    pdf = None
    if result["status"] == "checked" or result.get("pages"):
        try:
            pdf = intake(filename, data).pdf
        except Exception:
            pdf = None
    item_id = uuid.uuid4().hex[:12]
    ITEMS[item_id] = {"result": result, "pdf": pdf, "events": [], "pages": {}, "sample": sample}
    return _summary(item_id, ITEMS[item_id])


def _get(item_id: str) -> dict:
    item = ITEMS.get(item_id)
    if item is None:
        raise HTTPException(404, "Unknown item")
    return item


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/items")
def list_items():
    return [_summary(i, it) for i, it in ITEMS.items()]


@app.delete("/api/items")
def clear_items():
    ITEMS.clear()
    return {"ok": True}


@app.post("/api/check")
async def upload(files: list[UploadFile] = File(...)):
    out = []
    for f in files[:25]:
        data = await f.read(MAX_FILE_BYTES + 1)
        out.append(_add(Path(f.filename or "upload").name, data))
    return out


@app.post("/api/samples")
def load_samples():
    """Six synthetic invoices built on the spot: nothing here is a real company or account."""
    from .synth import BASES, build_spec, build_xml, m_iban, m_line_amount, m_number, make_hybrid, totals

    def pair(base_i, layout, mutate=None, side="pdf"):
        spec = build_spec(BASES[base_i])
        changed = copy.deepcopy(spec)
        if mutate:
            mutate(changed, 1)
        xml_spec, pdf_spec = (changed, spec) if side == "xml" else (spec, changed)
        return make_hybrid(xml_spec, pdf_spec, layout)

    broken = build_spec(BASES[0])
    t = totals(broken)
    broken["overrides"].update(gross=t["gross"] + 5, due=t["due"] + 5)  # XML arithmetic no longer adds up
    samples = [
        ("sample-1-clean.pdf", pair(0, "A"), "clean"),
        ("sample-2-bank-account-differs.pdf", pair(1, "B", m_iban), "iban"),
        ("sample-3-line-amount-differs.pdf", pair(2, "A", m_line_amount), "line"),
        ("sample-4-credit-note-two-rates.pdf", pair(3, "C"), "benign"),
        ("sample-5-unfamiliar-layout.pdf", pair(4, "D", m_number), "inconclusive"),
        ("sample-6-rule-error.xml", build_xml(broken), "rules"),
    ]
    ITEMS.clear()
    return [_add(name, data, sample=tag) for name, data, tag in samples]


@app.get("/api/items/{item_id}")
def get_item(item_id: str):
    item = _get(item_id)
    return {"summary": _summary(item_id, item), "result": item["result"], "events": item["events"]}


@app.get("/api/items/{item_id}/page/{number}.png")
def page_image(item_id: str, number: int):
    item = _get(item_id)
    if item["pdf"] is None or not (1 <= number <= len(item["result"].get("pages", []))):
        raise HTTPException(404, "No such page")
    if number not in item["pages"]:
        item["pages"][number] = render_page_png(item["pdf"], number)
    return Response(item["pages"][number], media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


class Review(BaseModel):
    decision: str
    note: str = ""


@app.post("/api/items/{item_id}/review")
def review(item_id: str, body: Review):
    item = _get(item_id)
    if body.decision not in DECISIONS:
        raise HTTPException(422, "Unknown decision")
    event = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "decision": body.decision,
             "note": body.note.strip()[:2000], "file_sha256": item["result"]["file"].get("sha256")}
    item["events"].append(event)  # append-only: earlier decisions stay in the record
    return {"events": item["events"]}


@app.get("/api/items/{item_id}/evidence")
def evidence(item_id: str):
    item = _get(item_id)
    bundle = {"kind": "receipt-workbench evidence record", "result": item["result"], "review_events": item["events"],
              "disclaimer": "Technical consistency check of the PDF text layer against the embedded XML. "
                            "Not tax advice, not a fraud check, not a payment approval."}
    name = Path(item["result"]["file"]["name"]).stem + ".evidence.json"
    return JSONResponse(json.loads(json.dumps(bundle, ensure_ascii=False)),
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


def cmd_serve(args) -> int:
    import uvicorn

    print(f"Receipt Workbench on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
