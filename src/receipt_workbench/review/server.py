from __future__ import annotations

import copy
import json
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from ..intake.ingest import intake
from ..page_text.pdftext import render_page_png
from ..pipeline.check import check_file, needs_review
from .limits import Inboxes, ReviewLimits, Settings, security_headers

STATIC = Path(__file__).parent / "static"
DECISIONS = {"reviewed", "needs_clarification"}
RENDER_LOCK = Lock()

router = APIRouter()


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
        "mismatches": [{"key": f["key"], "line": f.get("line"), "cells": f.get("cells")}
                       for f in comp.get("findings") or [] if f.get("status") == "mismatch"],
        "needs_review": needs_review(r), "sample": item.get("sample"),
        "decision": item["events"][-1]["decision"] if item["events"] else None,
    }


def _build_item(filename: str, data: bytes, sample: str | None = None) -> dict:
    result = check_file(filename, data)
    pdf = None
    if result["status"] == "checked" or result.get("pages"):
        try:
            pdf = intake(filename, data).pdf
        except Exception:
            pdf = None
    return {"result": result, "pdf": pdf, "events": [], "pages": {}, "sample": sample}


def _get(request: Request, item_id: str) -> dict:
    item = request.state.inbox.items.get(item_id)
    if item is None:
        raise HTTPException(404, "Unknown item")
    return item


@router.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/api/items")
async def list_items(request: Request):
    return [_summary(i, it) for i, it in request.state.inbox.items.items()]


@router.delete("/api/items")
async def clear_items(request: Request):
    request.app.state.inboxes.replace(request.state.inbox, {})
    return {"ok": True}


@router.post("/api/check")
async def upload(request: Request):
    store = request.app.state.inboxes
    settings = store.settings
    try:
        form = await request.form(max_files=settings.max_files, max_fields=0)
    except StarletteHTTPException as error:
        if isinstance(error.__context__, MultiPartException) and str(error.detail).startswith("Too many"):
            raise HTTPException(413, "Too many files or form fields in this upload.") from error
        raise
    try:
        files = form.getlist("files")
        if not files or any(not isinstance(uploaded, UploadFile) for uploaded in files):
            raise HTTPException(422, "Choose PDF or XML files to check.")
        if len(files) != len(form.multi_items()):
            raise HTTPException(422, "Use the files field for uploads.")
        if len(files) + len(request.state.inbox.items) > settings.max_items:
            raise HTTPException(429, "Inbox is full. Clear it first.")
        if any(uploaded.size is not None and uploaded.size > settings.max_file_bytes for uploaded in files):
            raise HTTPException(413, "File is too large. Please choose a smaller file.")
        out = []
        for uploaded in files:
            data = await uploaded.read(settings.max_file_bytes + 1)
            if len(data) > settings.max_file_bytes:
                raise HTTPException(413, "File is too large. Please choose a smaller file.")
            item = await run_in_threadpool(_build_item, Path(uploaded.filename or "upload").name, data)
            item_id = uuid.uuid4().hex
            with store.lock:
                if len(request.state.inbox.items) >= settings.max_items:
                    raise HTTPException(429, "Inbox is full. Clear it first.")
                store.replace(request.state.inbox, {**request.state.inbox.items, item_id: item})
            out.append(_summary(item_id, item))
        return out
    finally:
        await form.close()


def _samples() -> list[tuple[str, bytes, str]]:
    """Six synthetic invoices built on the spot: nothing here is a real company or account."""
    from ..testset.synth import BASES, build_spec, build_xml, m_iban, m_line_amount, m_number, make_hybrid, totals

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
    return [
        ("sample-1-clean.pdf", pair(0, "A"), "clean"),
        ("sample-2-bank-account-differs.pdf", pair(1, "B", m_iban), "iban"),
        ("sample-3-line-amount-differs.pdf", pair(2, "A", m_line_amount), "line"),
        ("sample-4-credit-note-two-rates.pdf", pair(3, "C"), "benign"),
        ("sample-5-unfamiliar-layout.pdf", pair(4, "D", m_number), "inconclusive"),
        ("sample-6-rule-error.xml", build_xml(broken), "rules"),
    ]


@router.post("/api/samples")
async def load_samples(request: Request):
    store = request.app.state.inboxes
    if store.settings.max_items < 6:
        raise HTTPException(429, "Inbox is too small for the six sample invoices.")
    samples = await run_in_threadpool(_samples)
    items = {}
    for name, data, tag in samples:
        if len(data) > store.settings.max_file_bytes:
            raise HTTPException(413, "File is too large. Please choose a smaller file.")
        items[uuid.uuid4().hex] = await run_in_threadpool(_build_item, name, data, tag)
    store.replace(request.state.inbox, items)
    return [_summary(item_id, item) for item_id, item in items.items()]


@router.get("/api/items/{item_id}")
async def get_item(request: Request, item_id: str):
    item = _get(request, item_id)
    return {"summary": _summary(item_id, item), "result": item["result"], "events": item["events"]}


@router.get("/api/items/{item_id}/page/{number}.png")
async def page_image(request: Request, item_id: str, number: int):
    item = _get(request, item_id)
    if item["pdf"] is None or not (1 <= number <= len(item["result"].get("pages", []))):
        raise HTTPException(404, "No such page")
    if number not in item["pages"]:
        store = request.app.state.inboxes
        image = await run_in_threadpool(_render_page, item["pdf"], number)
        with store.lock:
            item = _get(request, item_id)
            item = {**item, "pages": {**item["pages"], number: image}}
            store.replace(request.state.inbox, {**request.state.inbox.items, item_id: item})
    return Response(item["pages"][number], media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


def _render_page(pdf: bytes, number: int) -> bytes:
    with RENDER_LOCK:  # PDFium is not thread-safe
        return render_page_png(pdf, number)


class Review(BaseModel):
    decision: str
    note: str = ""


@router.post("/api/items/{item_id}/review")
async def review(request: Request, item_id: str, body: Review):
    item = _get(request, item_id)
    if body.decision not in DECISIONS:
        raise HTTPException(422, "Unknown decision")
    event = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "decision": body.decision,
             "note": body.note.strip()[:2000], "file_sha256": item["result"]["file"].get("sha256")}
    store = request.app.state.inboxes
    with store.lock:
        item = _get(request, item_id)
        item = {**item, "events": [*item["events"], event]}
        store.replace(request.state.inbox, {**request.state.inbox.items, item_id: item})
    return {"events": item["events"]}


@router.get("/api/items/{item_id}/evidence")
async def evidence(request: Request, item_id: str):
    item = _get(request, item_id)
    bundle = {"kind": "receipt-workbench evidence record", "result": item["result"], "review_events": item["events"],
              "disclaimer": "Technical consistency check of the PDF text layer against the embedded XML. "
                            "Not tax advice, not a fraud check, not a payment approval."}
    name = Path(item["result"]["file"]["name"]).stem + ".evidence.json"
    return JSONResponse(json.loads(json.dumps(bundle, ensure_ascii=False)),
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


def create_app(settings: Settings | None = None, clock: Callable[[], float] = time.monotonic) -> FastAPI:
    settings = settings or Settings.from_env()
    application = FastAPI(title="Receipt Workbench", docs_url=None, redoc_url=None)
    application.state.inboxes = Inboxes(settings, clock)
    headers = security_headers(STATIC / "index.html") if settings.public else {}

    @application.exception_handler(Exception)
    async def server_error(request: Request, error: Exception):
        return JSONResponse({"detail": "The server could not process this request."}, status_code=500,
                            headers={**headers, "Cache-Control": "no-store"})

    application.add_middleware(ReviewLimits, store=application.state.inboxes, headers=headers)
    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    application.include_router(router)
    return application


app = create_app()


def cmd_serve(args) -> int:
    import uvicorn

    print(f"Receipt Workbench on http://{args.host}:{args.port}")
    uvicorn.run(create_app(Settings.from_env(public=args.public)), host=args.host, port=args.port,
                log_level="warning", proxy_headers=False)
    return 0
