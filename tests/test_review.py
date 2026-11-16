"""Local review screen and API integration tests."""

import pytest
from fastapi.testclient import TestClient

from receipt_workbench.review.limits import Settings


@pytest.mark.parametrize("public", [False, True])
def test_server_flow(public):
    from receipt_workbench.review.server import create_app

    application = create_app(Settings.from_env(public=public))
    c = TestClient(application)
    assert c.get("/").status_code == 200
    assert "XRechnung rules (KoSIT)" in c.get("/").text
    assert "XRechnung-Regeln (KoSIT)" in c.get("/").text
    samples = c.post("/api/samples").json()
    assert len(samples) == 6
    by = {s["sample"]: s for s in samples}
    assert by["clean"]["verdict"] == "no_difference_in_checked_fields" and not by["clean"]["needs_review"]
    assert by["clean"]["validation"]["xrechnung"] == "not_run"
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
    assert bundle["result"]["validation"][2]["name"] == "xrechnung"
    assert bundle["result"]["validation"][2]["outcome"] == "not_run"
    up = c.post("/api/check", files=[("files", ("n.txt", b"x", "text/plain"))]).json()
    assert up[0]["status"] == "unsupported"
    other = TestClient(application)
    assert other.get("/api/items").json() == []
    for suffix in ("", "/page/1.png", "/evidence"):
        response = other.get(f"/api/items/{item}{suffix}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Unknown item"}
    response = other.post(f"/api/items/{item}/review", json={"decision": "reviewed"})
    assert response.status_code == 404 and response.json() == {"detail": "Unknown item"}
    assert other.post("/api/samples").status_code == 200
    assert {entry["id"] for entry in other.get("/api/items").json()}.isdisjoint(
        entry["id"] for entry in c.get("/api/items").json())
    assert other.delete("/api/items").status_code == 200
    assert len(c.get("/api/items").json()) == 7
    assert c.get(f"/api/items/{item}").status_code == 200
