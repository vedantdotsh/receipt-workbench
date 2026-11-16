"""Local review screen and API integration tests."""

from fastapi.testclient import TestClient


def test_server_flow():
    from receipt_workbench.review.server import ITEMS, app

    ITEMS.clear()
    c = TestClient(app)
    assert c.get("/").status_code == 200
    samples = c.post("/api/samples").json()
    assert len(samples) == 6
    by = {s["sample"]: s for s in samples}
    assert by["clean"]["verdict"] == "no_difference_in_checked_fields" and not by["clean"]["needs_review"]
    assert by["iban"]["verdict"] == "difference_found"
    assert by["line"]["verdict"] == "difference_found"
    assert by["benign"]["verdict"] == "no_difference_in_checked_fields"
    assert by["inconclusive"]["needs_review"] or by["inconclusive"]["verdict"] == "no_difference_in_checked_fields"
    assert by["rules"]["validation"]["rules"] == "failed"
    item = by["iban"]["id"]
    assert c.get(f"/api/items/{item}/page/1.png").headers["content-type"] == "image/png"
    assert c.get(f"/api/items/{item}/page/9.png").status_code == 404
    ev = c.post(f"/api/items/{item}/review", json={"decision": "needs_clarification", "note": "ask supplier"}).json()
    assert ev["events"][0]["decision"] == "needs_clarification"
    assert c.post(f"/api/items/{item}/review", json={"decision": "pay"}).status_code == 422
    bundle = c.get(f"/api/items/{item}/evidence").json()
    assert bundle["review_events"] and bundle["result"]["file"]["sha256"]
    up = c.post("/api/check", files=[("files", ("n.txt", b"x", "text/plain"))]).json()
    assert up[0]["status"] == "unsupported"
