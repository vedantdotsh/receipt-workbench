# Roadmap

Receipt Workbench is under active development. This page says what is released,
and what is still missing. Order and scope can change.

Last updated: 25 November 2026.

Hosted app: <https://receipt-workbench.madebyved.com> (sign-in required)

## Two parts

- **This repository** holds the checking engine, the CLI and a local review
  screen for one user. It keeps its state in memory.
- **The hosted app** is the team version built on the same engine. It adds
  accounts, workspaces and stored invoices. Its code is not part of this
  repository, so the team features below cannot be run from here.

## Released in this repository

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

## Released in the hosted app

These were listed as in progress or planned in the first version of this page.
They are now live.

- **Accounts.** Google sign-in and one-time email links.
- **Workspaces.** Admin, Member and Viewer roles, with invites.
- **Stored invoices.** Invoices and check results are kept per workspace and
  survive a restart. Each checked field is highlighted on the invoice page.
- **Workspace overview.** One screen for the invoices that still need
  attention, grouped by kind of problem.
- **Team rules.** A team writes its own checking rules in a shared draft. A
  preview shows which invoices the draft would flag. An Admin publishes it as a
  numbered version, and an earlier version can be restored.
- **Storage limits.** Each account has a storage limit across its workspaces,
  with a usage screen and a warning before the limit is reached.
- **Assistant.** Optional. Finds invoices or proposes a rule change from a
  question in plain words, using Mistral to interpret the question. It is off
  until a workspace Admin turns it on, each user has a daily allowance, and a
  rule change is only applied after the user confirms it.
- **Languages.** English and German interface.

Additional stack in the hosted app: SQLAlchemy 2, Alembic, SQLite, React,
TypeScript, Vite, React Router, Vitest, Resend for sign-in email.

## Known gaps, not scheduled yet

- OCR for scanned invoices. Today only the PDF text layer is compared.
- UBL invoices. Today only CII syntax is parsed.
- Line-by-line comparison of allowances and charges.

See [limits and non-goals](docs/details.md#limits-and-non-goals) for the
current limits in full.
