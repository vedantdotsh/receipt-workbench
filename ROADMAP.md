# Roadmap

Receipt Workbench is under active development. This page says what is released,
what is being built and what is planned. It is a plan, not a promise: order and
scope can change.

Live demo: <https://receipt-workbench.madebyved.com>

## Released (public demo)

- Checking engine for hybrid e-invoices (ZUGFeRD 2.x, Factur-X, XRechnung in
  CII syntax): XSD and Schematron validation, and comparison of the embedded
  data with the PDF text layer.
- Web review screen, CLI, JSON export and a plug-in interface for parsers,
  page readers, validators and exports.
- Synthetic test-set generator and a recorded benchmark
  ([results](benchmark/RESULTS.md)).
- Docker Compose deployment behind Caddy or a Cloudflare Tunnel.

Stack: Python 3.13, FastAPI, Uvicorn, lxml, Saxon (saxonche) for Schematron,
factur-x, pdfplumber, pypdf, pypdfium2, ReportLab, pytest, Ruff, uv, Docker,
Caddy.

## In progress (team foundation)

The demo keeps everything in memory for one anonymous visitor. The next step is
a version a small team can use together.

- Database layer with SQLAlchemy 2, Alembic migrations and SQLite in WAL mode.
- Workspaces, memberships with Admin, Member and Viewer roles, and invites.
- Request security for a hosted mode: signed sessions, CSRF and Origin checks,
  body limits and authentication required by default on every API route.
- New front end in React, TypeScript and Vite with React Router, tested with
  Vitest and Testing Library.

## Planned

- **Accounts.** Google sign-in and one-time email links (Resend).
- **Stored invoices.** Invoices and check results kept per workspace instead of
  in memory, so they survive a restart.
- **Storage limits.** Per-workspace and per-instance quotas with a notification
  before the limit is reached.
- **Team rules.** Checking rules a team can set for its own workspace.
- **Assistant.** Optional help with reading a result, through OpenRouter. Off
  by default for every workspace, with a monthly spend cap.

## Known gaps, not scheduled yet

- OCR for scanned invoices. Today only the PDF text layer is compared.
- UBL invoices. Today only CII syntax is parsed.
- Line-by-line comparison of allowances and charges.

See [limits and non-goals](docs/details.md#limits-and-non-goals) for the
current limits in full.
