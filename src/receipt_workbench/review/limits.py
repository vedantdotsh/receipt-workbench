from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import secrets
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from threading import BoundedSemaphore, RLock

from fastapi import HTTPException
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..intake.ingest import MAX_FILE_BYTES

COOKIE = "rw_session"
CHECK_PATHS = {"/api/check", "/api/samples"}


@dataclass(frozen=True)
class Settings:
    public: bool = False
    max_file_bytes: int = MAX_FILE_BYTES
    max_files: int = 25
    max_items: int = 200
    max_request_bytes: int = 25 * MAX_FILE_BYTES + 1024 * 1024
    max_visitors: int = 1000
    max_memory_bytes: int = 1024 * 1024 * 1024
    idle_seconds: int = 0
    visitor_rate: int = 0
    ip_rate: int = 0
    rate_window_seconds: int = 60
    max_concurrent_checks: int = 1
    trust_proxy: bool = False

    def __post_init__(self) -> None:
        for setting in fields(self):
            value = getattr(self, setting.name)
            if isinstance(value, bool):
                continue
            minimum = 0 if setting.name in {"idle_seconds", "visitor_rate", "ip_rate"} else 1
            if value < minimum:
                raise ValueError(f"{setting.name} must be at least {minimum}")
        if self.max_file_bytes > MAX_FILE_BYTES:
            raise ValueError(f"max_file_bytes cannot exceed the intake limit of {MAX_FILE_BYTES}")

    @classmethod
    def from_env(cls, public: bool | None = None) -> Settings:
        def boolean(name: str, default: bool) -> bool:
            value = os.environ.get(name, str(default)).lower()
            if value not in {"true", "false", "1", "0"}:
                raise ValueError(f"{name} must be true, false, 1 or 0")
            return value in {"true", "1"}

        public = boolean("RW_PUBLIC", False) if public is None else public
        defaults = cls(public=True, max_file_bytes=5 * 1024 * 1024, max_files=5, max_items=20,
                       max_request_bytes=20 * 1024 * 1024, max_visitors=100,
                       max_memory_bytes=128 * 1024 * 1024, idle_seconds=1800,
                       visitor_rate=10, ip_rate=30) if public else cls()
        values = {setting.name: int(os.environ.get(f"RW_{setting.name.upper()}", getattr(defaults, setting.name)))
                  for setting in fields(cls) if setting.name not in {"public", "trust_proxy"}}
        return cls(public=public, trust_proxy=boolean("RW_TRUST_PROXY", False), **values)


@dataclass
class Inbox:
    accessed: float
    items: dict[str, dict] = field(default_factory=dict)
    checks: deque[float] = field(default_factory=deque)
    active: int = 0


def _size(value: object, seen: set[int]) -> int:
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_size(key, seen) + _size(item, seen) for key, item in value.items())
    elif isinstance(value, (list, tuple, deque)):
        size += sum(_size(item, seen) for item in value)
    elif isinstance(value, Inbox):
        size += _size(vars(value), seen)
    return size


def busy(detail: str) -> HTTPException:
    return HTTPException(503, detail, headers={"Retry-After": "1"})


class Inboxes:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.clock = clock
        self.visitors: dict[str, Inbox] = {}
        self.ip_checks: dict[str, deque[float]] = {}
        self.slots = BoundedSemaphore(settings.max_concurrent_checks)
        self.lock = RLock()

    @property
    def memory_bytes(self) -> int:
        return _size((self.visitors, self.ip_checks), set())

    def _expire(self) -> None:
        now = self.clock()
        for session, inbox in list(self.visitors.items()):
            if not inbox.active and self.settings.idle_seconds and now - inbox.accessed >= self.settings.idle_seconds:
                del self.visitors[session]
        cutoff = now - self.settings.rate_window_seconds
        for address, checks in list(self.ip_checks.items()):
            while checks and checks[0] <= cutoff:
                checks.popleft()
            if not checks:
                del self.ip_checks[address]

    def _evict(self, protected: Inbox) -> None:
        candidates = [(session, inbox) for session, inbox in self.visitors.items()
                      if inbox is not protected and not inbox.active]
        if not candidates:
            raise busy("Server memory is full. Please try again shortly.")
        session, _ = min(candidates, key=lambda pair: pair[1].accessed)
        del self.visitors[session]

    def _fit(self, protected: Inbox) -> None:
        while len(self.visitors) > self.settings.max_visitors or self.memory_bytes > self.settings.max_memory_bytes:
            self._evict(protected)

    def acquire(self, session: str | None) -> tuple[str, Inbox, bool]:
        with self.lock:
            self._expire()
            fresh = session not in self.visitors
            if fresh:
                session = secrets.token_urlsafe(32)
                self.visitors[session] = Inbox(self.clock())
            inbox = self.visitors[session]
            try:
                self._fit(inbox)
            except HTTPException:
                if fresh:
                    del self.visitors[session]
                raise
            inbox.active += 1
            inbox.accessed = self.clock()
            return session, inbox, fresh

    def release(self, inbox: Inbox) -> None:
        with self.lock:
            inbox.active -= 1
            inbox.accessed = self.clock()

    def replace(self, inbox: Inbox, items: dict[str, dict]) -> None:
        with self.lock:
            previous = inbox.items
            inbox.items = items
            try:
                self._fit(inbox)
            except HTTPException:
                inbox.items = previous
                raise

    def rate_limit(self, inbox: Inbox, address: str) -> None:
        with self.lock:
            self._expire()
            now = self.clock()
            windows = []
            if self.settings.visitor_rate:
                windows.append((inbox.checks, self.settings.visitor_rate))
            if self.settings.ip_rate:
                if address not in self.ip_checks and len(self.ip_checks) >= self.settings.max_visitors:
                    raise busy("Server is busy. Please try again shortly.")
                windows.append((self.ip_checks.get(address, deque()), self.settings.ip_rate))
            for checks, maximum in windows:
                while checks and checks[0] <= now - self.settings.rate_window_seconds:
                    checks.popleft()
                if len(checks) >= maximum:
                    retry = max(1, math.ceil(checks[0] + self.settings.rate_window_seconds - now))
                    raise HTTPException(429, "Too many checks. Please try again later.",
                                        headers={"Retry-After": str(retry)})
            for checks, _ in windows:
                checks.append(now)
            if self.settings.ip_rate:
                self.ip_checks[address] = windows[-1][0]
            try:
                self._fit(inbox)
            except HTTPException:
                for checks, _ in windows:
                    checks.pop()
                if address in self.ip_checks and not self.ip_checks[address]:
                    del self.ip_checks[address]
                raise


def security_headers(page: Path) -> dict[str, str]:
    html = page.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
                      for script in scripts)
    return {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (f"default-src 'none'; script-src 'self' {hashes}; script-src-attr 'none'; "
                                    "style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; "
                                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
    }


class ReviewLimits:
    def __init__(self, app: ASGIApp, store: Inboxes, headers: dict[str, str]):
        self.app = app
        self.store = store
        self.headers = headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = self.store.settings
        headers = Headers(scope=scope)
        inbox = None
        cookie = None
        admitted = False
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > settings.max_request_bytes:
                    raise HTTPException(413, "Request is too large. Please upload fewer or smaller files.")
            return message

        async def response_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                outgoing = MutableHeaders(scope=message)
                outgoing.update(self.headers)
                if scope["path"].startswith("/api/"):
                    outgoing["Cache-Control"] = "no-store"
                if cookie:
                    outgoing.append("Set-Cookie", cookie)
            await send(message)

        try:
            if scope["path"] == "/" or scope["path"].startswith("/api/"):
                session, inbox, fresh = self.store.acquire(Request(scope).cookies.get(COOKIE))
                scope.setdefault("state", {})["inbox"] = inbox
                if fresh:
                    secure = scope["scheme"] == "https" or (settings.trust_proxy and
                                                              headers.get("x-forwarded-proto") == "https")
                    response = Response()
                    response.set_cookie(COOKIE, session, httponly=True, samesite="lax", secure=secure)
                    cookie = response.headers["set-cookie"]
                if scope["method"] == "POST" and scope["path"] in CHECK_PATHS:
                    address = scope["client"][0] if scope.get("client") else "unknown"
                    if settings.trust_proxy and headers.get("x-forwarded-for"):
                        address = headers["x-forwarded-for"].split(",")[0].strip()
                    self.store.rate_limit(inbox, address)
                    admitted = self.store.slots.acquire(blocking=False)
                    if not admitted:
                        raise busy("All check slots are busy. Please try again shortly.")
                length = headers.get("content-length")
                if length and length.isdecimal() and int(length) > settings.max_request_bytes:
                    raise HTTPException(413, "Request is too large. Please upload fewer or smaller files.")
            await self.app(scope, limited_receive, response_send)
        except HTTPException as error:
            await JSONResponse({"detail": error.detail}, status_code=error.status_code,
                               headers=error.headers)(scope, receive, response_send)
        finally:
            if admitted:
                self.store.slots.release()
            if inbox is not None:
                self.store.release(inbox)
