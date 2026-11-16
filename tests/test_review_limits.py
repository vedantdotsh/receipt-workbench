import asyncio
import base64
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from threading import Event

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from receipt_workbench.review import server
from receipt_workbench.review.limits import COOKIE, Inboxes, Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv("RW_PUBLIC", raising=False)
    for setting in fields(Settings):
        monkeypatch.delenv(f"RW_{setting.name.upper()}", raising=False)


def upload(client, data=b"x", count=1, **kwargs):
    return client.post("/api/check", files=[("files", ("invoice.pdf", data))] * count, **kwargs)


def refusal(response, status):
    assert response.status_code == status, response.text
    assert isinstance(response.json()["detail"], str)
    if status in (429, 503) and "Inbox" not in response.json()["detail"]:
        assert int(response.headers["Retry-After"]) > 0


def test_settings_defaults_and_environment(monkeypatch):
    local = Settings.from_env()
    public = Settings.from_env(public=True)
    assert (local.max_file_bytes, local.max_files, local.max_items) == (15 * 1024**2, 25, 200)
    assert (local.idle_seconds, local.visitor_rate, local.ip_rate) == (0, 0, 0)
    assert (public.max_file_bytes, public.max_files, public.max_items) == (5 * 1024**2, 5, 20)
    assert (public.idle_seconds, public.visitor_rate, public.ip_rate) == (1800, 10, 30)
    monkeypatch.setenv("RW_PUBLIC", "1")
    monkeypatch.setenv("RW_MAX_FILES", "3")
    monkeypatch.setenv("RW_TRUST_PROXY", "true")
    assert Settings.from_env() == replace(public, max_files=3, trust_proxy=True)
    assert not Settings.from_env(public=False).public
    for setting in fields(Settings):
        if setting.name not in {"public", "trust_proxy"}:
            monkeypatch.setenv(f"RW_{setting.name.upper()}", "2")
    configured = Settings.from_env()
    assert all(getattr(configured, setting.name) == 2 for setting in fields(Settings)
               if setting.name not in {"public", "trust_proxy"})


@pytest.mark.parametrize("name,value", [("RW_PUBLIC", "maybe"), ("RW_TRUST_PROXY", "maybe"),
                                       ("RW_MAX_FILES", "0"), ("RW_IDLE_SECONDS", "-1"),
                                       ("RW_MAX_FILE_BYTES", str(16 * 1024**2))])
def test_settings_reject_invalid_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Settings.from_env()


@pytest.mark.parametrize("secure", [False, True])
def test_cookie_is_opaque_and_cannot_fix_session(secure):
    client = TestClient(server.create_app(Settings()), base_url="https://testserver" if secure else "http://testserver")
    client.cookies.set(COOKIE, "chosen-session")
    response = client.get("/")
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert ("Secure" in cookie) is secure
    session = cookie.split(";", 1)[0].split("=", 1)[1]
    assert session != "chosen-session" and len(session) >= 40
    assert "set-cookie" not in client.get("/api/items").headers


@pytest.mark.parametrize("options,status", [({"max_file_bytes": 1}, 413), ({"max_files": 1}, 413),
                                           ({"max_items": 1}, 429), ({"max_request_bytes": 10}, 413)])
def test_upload_limits(options, status):
    client = TestClient(server.create_app(replace(Settings(), **options)))
    refusal(upload(client, b"xx", count=2), status)
    assert client.get("/api/items").json() == []


def test_item_capacity_and_clear():
    client = TestClient(server.create_app(Settings(max_items=1)))
    assert upload(client).status_code == 200
    refusal(upload(client), 429)
    client.delete("/api/items")
    assert upload(client).status_code == 200
    refusal(client.post("/api/samples"), 429)


def test_local_upload_accepts_25_files():
    client = TestClient(server.create_app(Settings()))
    assert len(upload(client, count=25).json()) == 25
    refusal(upload(client, count=26), 413)


@pytest.mark.parametrize("declared", [None, "1"])
def test_streamed_body_stops_at_limit(declared):
    application = server.create_app(Settings(max_request_bytes=10))
    sent = []
    reads = 0

    async def receive():
        nonlocal reads
        reads += 1
        assert reads <= 2
        return {"type": "http.request", "body": b"--test", "more_body": True}

    async def send(message):
        sent.append(message)

    headers = [(b"content-type", b"multipart/form-data; boundary=test")]
    if declared:
        headers.append((b"content-length", declared.encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/api/check", "raw_path": b"/api/check", "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 123), "server": ("testserver", 80)}
    asyncio.run(application(scope, receive, send))
    assert reads == 2
    assert next(message["status"] for message in sent if message["type"] == "http.response.start") == 413
    assert all(inbox.active == 0 for inbox in application.state.inboxes.visitors.values())
    assert application.state.inboxes.slots.acquire(blocking=False)
    application.state.inboxes.slots.release()


def test_expiry_and_local_persistence():
    now = [0.0]
    for idle in (0, 30):
        application = server.create_app(Settings(idle_seconds=idle), clock=lambda: now[0])
        client = TestClient(application)
        item = upload(client).json()[0]["id"]
        original_cookie = client.cookies.get(COOKIE)
        now[0] += 30
        assert client.get(f"/api/items/{item}").status_code == (404 if idle else 200)
        assert (client.cookies.get(COOKIE) != original_cookie) is bool(idle)


def test_oldest_idle_visitor_eviction():
    now = [0.0]
    application = server.create_app(Settings(max_visitors=2), clock=lambda: now[0])
    first, second, third = (TestClient(application) for _ in range(3))
    first_item = upload(first).json()[0]["id"]
    now[0] = 1
    second_item = upload(second).json()[0]["id"]
    now[0] = 2
    assert first.get(f"/api/items/{first_item}").status_code == 200
    now[0] = 3
    third.get("/")
    assert len(application.state.inboxes.visitors) == 2
    assert first.get(f"/api/items/{first_item}").status_code == 200
    assert second.get(f"/api/items/{second_item}").status_code == 404


def test_active_visitors_are_not_evicted():
    store = Inboxes(Settings(max_visitors=1))
    session, inbox, _ = store.acquire(None)
    with pytest.raises(HTTPException) as caught:
        store.acquire(None)
    assert caught.value.status_code == 503
    assert session in store.visitors and len(store.visitors) == 1
    store.release(inbox)
    assert store.acquire(None)[0] != session


def test_visitor_capacity_refuses_requests_while_pinned():
    application = server.create_app(Settings(max_visitors=1))
    store = application.state.inboxes
    session, inbox, _ = store.acquire(None)
    client = TestClient(application)
    refusal(client.get("/api/items"), 503)
    assert session in store.visitors
    store.release(inbox)
    assert client.get("/api/items").status_code == 200
    assert session not in store.visitors


def test_memory_cap_evicts_oldest_and_rolls_back():
    now = [0.0]
    store = Inboxes(Settings(max_memory_bytes=12000), clock=lambda: now[0])
    first_session, first, _ = store.acquire(None)
    store.replace(first, {"first": {"pdf": b"a" * 5000}})
    store.release(first)
    now[0] = 1
    second_session, second, _ = store.acquire(None)
    store.replace(second, {"second": {"pdf": b"b" * 6000}})
    assert first_session not in store.visitors and second_session in store.visitors
    assert store.memory_bytes <= 12000
    with pytest.raises(HTTPException) as caught:
        store.replace(second, {"large": {"pdf": b"c" * 12000}})
    assert caught.value.status_code == 503
    assert "second" in second.items and store.memory_bytes <= 12000


def test_memory_refusal_and_headers():
    application = server.create_app(replace(Settings.from_env(public=True), max_memory_bytes=1))
    client = TestClient(application)
    response = client.get("/api/items")
    refusal(response, 503)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert not application.state.inboxes.visitors


def test_page_and_review_memory_are_bounded(hybrid, monkeypatch):
    application = server.create_app(Settings(max_memory_bytes=500000))
    client = TestClient(application)
    item = upload(client, hybrid()).json()[0]["id"]
    store = application.state.inboxes
    inbox = next(iter(store.visitors.values()))
    monkeypatch.setattr(server, "render_page_png", lambda *args: b"a" * 500000)
    refusal(client.get(f"/api/items/{item}/page/1.png"), 503)
    assert inbox.items[item]["pages"] == {}
    monkeypatch.setattr(store, "settings", replace(store.settings, max_memory_bytes=store.memory_bytes + 100))
    refusal(client.post(f"/api/items/{item}/review", json={"decision": "reviewed", "note": "x" * 2000}), 503)
    assert inbox.items[item]["events"] == []
    assert store.memory_bytes <= store.settings.max_memory_bytes


def test_visitor_rate_shared_by_checking_endpoints():
    now = [0.0]
    application = server.create_app(Settings(visitor_rate=1), clock=lambda: now[0])
    client = TestClient(application)
    assert upload(client).status_code == 200
    response = client.post("/api/samples")
    refusal(response, 429)
    assert response.headers["Retry-After"] == "60"
    assert client.get("/api/items").status_code == 200
    assert TestClient(application).post("/api/check").status_code == 422
    now[0] = 60
    assert upload(client).status_code == 200


@pytest.mark.parametrize("trusted", [False, True])
def test_proxy_ip_headers_only_when_trusted(trusted):
    now = [0.0]
    application = server.create_app(Settings(ip_rate=1, trust_proxy=trusted), clock=lambda: now[0])
    first, second = TestClient(application), TestClient(application)
    assert upload(first, headers={"X-Forwarded-For": "192.0.2.1, 127.0.0.1"}).status_code == 200
    response = upload(second, headers={"X-Forwarded-For": "192.0.2.2, 127.0.0.1"})
    assert response.status_code == (200 if trusted else 429)
    refusal(upload(TestClient(application), headers={"X-Forwarded-For": "192.0.2.1, 127.0.0.1"}), 429)
    now[0] = 60
    assert upload(second).status_code == 200


def test_ip_windows_survive_visitor_eviction_and_expire():
    now = [0.0]
    application = server.create_app(Settings(max_visitors=1, ip_rate=1), clock=lambda: now[0])
    assert upload(TestClient(application)).status_code == 200
    refusal(upload(TestClient(application)), 429)
    now[0] = 60
    assert upload(TestClient(application)).status_code == 200


def test_ip_metadata_is_bounded():
    now = [0.0]
    application = server.create_app(Settings(max_visitors=1, ip_rate=1, trust_proxy=True), clock=lambda: now[0])
    assert upload(TestClient(application), headers={"X-Forwarded-For": "192.0.2.1"}).status_code == 200
    refusal(upload(TestClient(application), headers={"X-Forwarded-For": "192.0.2.2"}), 503)
    assert len(application.state.inboxes.ip_checks) == 1
    now[0] = 60
    assert upload(TestClient(application), headers={"X-Forwarded-For": "192.0.2.2"}).status_code == 200


def test_checks_are_rejected_without_queuing(monkeypatch):
    entered, finish = Event(), Event()
    original = server._build_item

    def blocked(*args):
        entered.set()
        assert finish.wait(timeout=10)
        return original(*args)

    monkeypatch.setattr(server, "_build_item", blocked)
    application = server.create_app(Settings())
    first, second = TestClient(application), TestClient(application)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(upload, first)
        try:
            assert entered.wait(timeout=10)
            refusal(upload(second), 503)
            refusal(second.post("/api/samples"), 503)
            assert second.get("/api/items").status_code == 200
        finally:
            finish.set()
        assert future.result(timeout=10).status_code == 200
    assert upload(second).status_code == 200


@pytest.mark.parametrize("path", ["/", "/static/vendor/torph.mjs", "/missing", "/api/items", "/api/items/missing"])
def test_public_headers(path):
    client = TestClient(server.create_app(Settings.from_env(public=True)))
    response = client.get(path)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Content-Security-Policy" in response.headers
    if path.startswith("/api/"):
        assert response.headers["Cache-Control"] == "no-store"


def test_csp_matches_actual_inline_script_and_resources():
    client = TestClient(server.create_app(Settings.from_env(public=True)))
    response = client.get("/")
    policy = response.headers["Content-Security-Policy"]
    for script in re.findall(r"<script>(.*?)</script>", response.text.replace("\r\n", "\n"), re.DOTALL):
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in policy
    assert "script-src 'self'" in policy and "script-src-attr 'none'" in policy
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "img-src 'self'" in policy and "connect-src 'self'" in policy
    assert not re.search(r"\son\w+=", response.text)
    assert 'import("/static/vendor/torph.mjs")' in response.text
    assert "https://" not in response.text and "http://" not in response.text


def test_local_headers_and_disabled_rate_limits():
    client = TestClient(server.create_app(Settings()))
    assert "Content-Security-Policy" not in client.get("/").headers
    for _ in range(31):
        assert upload(client).status_code == 200


def test_internal_error_keeps_public_headers(monkeypatch):
    def broken(*args):
        raise RuntimeError("private details")

    monkeypatch.setattr(server, "_build_item", broken)
    client = TestClient(server.create_app(Settings.from_env(public=True)), raise_server_exceptions=False)
    response = upload(client)
    assert response.status_code == 500
    assert "private details" not in response.text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert client.app.state.inboxes.slots.acquire(blocking=False)
    client.app.state.inboxes.slots.release()


def test_serve_public_switch_and_no_implicit_proxy_trust(monkeypatch):
    import uvicorn

    from receipt_workbench.cli import main

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    assert main(["serve", "--public"]) == 0
    assert calls[-1][0].state.inboxes.settings.public
    assert calls[-1][1]["proxy_headers"] is False
    monkeypatch.setenv("RW_PUBLIC", "1")
    assert main(["serve"]) == 0
    assert calls[-1][0].state.inboxes.settings.public
