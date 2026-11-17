# Receipt Workbench

A hybrid e-invoice contains a readable PDF page and machine-readable invoice XML.
Valid XML can still disagree with the page.
Receipt Workbench is a Python web app and CLI that validates the XML and compares
it with the PDF text layer, reporting differences, uncertainty and field coverage.

Live demo: <https://receipt-workbench.madebyved.com>

## What it checks

- Formats: ZUGFeRD 2.x, Factur-X and XRechnung in UN/CEFACT CII syntax;
  ZUGFeRD 1.0 is best effort. Standalone XML has no page to compare.
- `schema`: profile-specific XSD validation.
- `rules`: Factur-X Schematron or CEN EN 16931 CII business rules.
- `xrechnung`: KoSIT XRechnung CII Schematron for declared XRechnung invoices.
- Page against data: identifiers, dates, parties, payments, totals, lines and VAT.

| Comparison verdict | Meaning |
|---|---|
| `difference_found` | Evidence of a difference in at least one field. |
| `no_difference_in_checked_fields` | No difference found; coverage requirements met. |
| `inconclusive` | Insufficient evidence or coverage. |
| `not_compared` | No usable PDF text layer, or XML-only input. |

Validation and comparison are separate results; skipped checks are not passes.
See [validation details and verdicts](docs/details.md#what-it-checks).

## Benchmark

Recorded results on 444 synthetic invoices:

- 0 false alarms on 32 clean invoices.
- 381 of 412 planted differences detected; 31 inconclusive, none wrongly passed.
- A baseline looking for invoice number, total and IBAN anywhere on the page
  detected 224 of 412 differences.

In the recorded 16 November run, Mustangproject and veraPDF accepted all 444
files and flagged none of the planted differences; they were not rerun after
the tax identifiers were replaced with deliberately invalid values.
The data is synthetic and the generator and checker share an author, so these
figures do not estimate accuracy on arbitrary invoices.
See [benchmark results and reproduction](benchmark/RESULTS.md).

## Quick start

With Python 3.13 and [uv](https://docs.astral.sh/uv/getting-started/installation/)
installed, run from the repository root:

```sh
uv sync
uv run receipt-workbench serve
```

Open <http://localhost:8321> and choose **Load sample invoices** for six examples.

```sh
uv run pytest -q
```

See [CLI usage and test-set commands](docs/details.md#quick-start).

## Limits

- No OCR: scans are not compared, and extracted text can differ from page content.
- Agreement is not proof of authenticity or payment approval.
- CII only; UBL needs a separate parser.
- In-memory state in one server process; inboxes disappear on restart.

See [full limits, project layout and deployment](docs/details.md#limits-and-non-goals)
and [plug-ins and environment variables](docs/extending.md).

## Licence

The project's own code is licensed under [Apache-2.0](LICENSE).
Third-party licences are listed in [docs/third-party.md](docs/third-party.md).
