# Receipt Workbench

A hybrid e-invoice contains a readable PDF page and machine-readable invoice XML.
Schema and business-rule validation can establish whether the XML follows a
published format, but a valid XML invoice can still disagree with the page.
Receipt Workbench is a Python web app and CLI that runs those validation layers
and compares the PDF text layer with the embedded data. It reports differences,
uncertainty and field coverage, with page positions and XML paths for review.

Live demo: <DEMO_URL>

## What it checks

The built-in parser reads UN/CEFACT CII: ZUGFeRD 2.x, Factur-X and XRechnung
in CII syntax. ZUGFeRD 1.0 parsing is best effort. Standalone XML can be
validated, but has no PDF page to compare. UBL needs a separate parser.

| Layer | Rules and publisher | When it runs |
|---|---|---|
| `schema` | Profile-specific Factur-X XSD, published by FNFE-MPE and FeRD and distributed by `factur-x`; legacy ZUGFeRD XSD for 1.0 | Recognised profiles |
| `rules` | Factur-X Schematron from the same library; CEN/TC 434 EN 16931 CII artefacts 1.3.16 for XRechnung | Supported CII profiles; the guideline in BT-24 selects the XRechnung route |
| `xrechnung` | KoSIT XRechnung CII Schematron 2.6.0 for XRechnung 3.0.2, including BR-DE and BR-DEX | CII invoices declaring XRechnung in BT-24 |

XRechnung runs the CEN rules before the KoSIT rules. Other profiles use the
library's Factur-X rules and report the XRechnung layer as `not_run`.
Schematron runs through SaxonC-HE; results identify its rules and engine. Each layer reports
`passed`, `failed`, `not_run` or `error`. Skips and engine errors are not passes.

The comparison covers invoice identifiers and dates, party and payment details,
totals, invoice lines and VAT breakdowns. It uses typed values and nearby labels,
and distinguishes a value confirmed at a label from one merely found on the page.
Findings retain the XML business term and XPath, plus available page evidence.
The review screen shows rendered pages and exports evidence and review decisions as JSON.

| Comparison verdict | Meaning |
|---|---|
| `difference_found` | At least one field has evidence of a difference. |
| `no_difference_in_checked_fields` | No difference was found and coverage requirements were met; some fields may remain unlocated. |
| `inconclusive` | Evidence or coverage is insufficient for the no-difference verdict. |
| `not_compared` | No usable PDF text layer is available, or the input is XML only. |

Validation and comparison are separate results. A valid XML document can have a
page difference, and agreement with the page does not establish XML validity.

## Benchmark

The recorded results in [benchmark/RESULTS.md](benchmark/RESULTS.md), dated
16 November 2026, use 444 synthetic hybrid invoices: eight base invoices in
four layouts, with 32 clean files and 412 files containing one planted difference.

- Clean files: 0 false alarms and 0 inconclusive results out of 32.
- Changed files: 381 of 412 differences detected on the intended field,
  31 inconclusive, 0 flagged on another field and 0 given a no-difference verdict.
- A baseline checking whether invoice number, total and IBAN occur anywhere on
  the page detects 224 of 412 differences.
- Layouts A to C: 309 of 309 detected. Layout D, with unfamiliar labels:
  72 of 103 detected and 31 inconclusive, including seven changed due dates.
- All 412 changed files still pass schema and rule validation.

In the recorded runs, Mustangproject 2.26.0 and veraPDF 1.30.4 accept all
444 files and flag none of the planted differences. Mustangproject also runs
veraPDF internally, so their PDF verdicts are not independent. These results
describe the tested configurations and files, not every capability of either tool.

The generator and checker share an author, and layouts A to C were used during
development. Layout D exercises unfamiliar labels, but these measurements do
not estimate accuracy on arbitrary invoices. Inconclusive cases are abstentions, not
detections. The linked results include scoring and reproduction details.

## Quick start

Install Python 3.13 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
From the repository root, install the package and development dependencies:

```sh
uv sync
```

Start the server, then open `http://localhost:8321`:

```sh
uv run receipt-workbench serve
```

The English and German screen accepts uploads. **Load sample invoices** creates six
examples with agreement, differences, an unfamiliar layout and a validation error.
Stop the server with Ctrl+C.

For a CLI example, generate the test set and check one clean invoice:

```sh
uv run receipt-workbench make-testset --out testset
uv run receipt-workbench check testset/clean/b01-simple_A_clean.pdf
```

Replace that path with your own PDF or XML. The CLI also accepts folders;
`-v` includes findings and `--json` prints the evidence record. The generated
files under `testset/` are ignored by Git. Run the tests with:

```sh
uv run pytest -q
```

## Connect your own parts

Install a package declaring entry points in one of these groups in the same environment.
The built-ins use the same registry.

| Entry-point group | Contract | Built-ins |
|---|---|---|
| `receipt_workbench.invoice_parsers` | XML root to `Invoice`; declares supported `flavors` | `cii` |
| `receipt_workbench.page_readers` | PDF bytes to `PdfText` with positions | `pdf_text` |
| `receipt_workbench.validators` | XML, flavour, profile and guideline to `Layer` | `schema`, `rules`, `xrechnung` |
| `receipt_workbench.exports` | Evidence and review events to `Export` | `json` |

See [harness/protocols.py](src/receipt_workbench/harness/protocols.py) for contracts.
An entry point exposes a callable or a class with no constructor arguments;
names must be unique within a group. Built-ins run first. The first compatible
parser and first active page reader are used; every active validator runs.
Exports are selected by name. List installed parts and load errors with:

```sh
uv run receipt-workbench parts
```

`RW_INVOICE_PARSERS`, `RW_PAGE_READERS`, `RW_VALIDATORS` and `RW_EXPORTS` restrict
each group to comma-separated names. `RW_DISABLE_INVOICE_PARSERS`,
`RW_DISABLE_PAGE_READERS`, `RW_DISABLE_VALIDATORS` and `RW_DISABLE_EXPORTS` remove
names; disabling wins. An empty restriction enables none. Restart after changes.
For example, `RW_VALIDATORS=schema,rules` selects two built-in validation layers.

## Project layout

Feature folders below are relative to `src/receipt_workbench/`.

- `cli.py`: command-line entry point.
- `common/`: shared value parsing and formatting.
- `harness/`: extension contracts, discovery and selection.
- `intake/`: classification, attachment extraction and XML intake checks.
- `invoice_data/`: CII parsing with business terms and XML paths.
- `page_text/`: PDF text, positions, rows and typed tokens.
- `comparison/`: field, line and VAT comparison, coverage and verdicts.
- `validation/`: schema and Schematron runners, pinned CEN and KoSIT artefacts.
- `pipeline/`: one file to one evidence record.
- `review/`: web interface, visitor inboxes, limits and JSON export.
- `testset/`: synthetic invoice generation and benchmark runners.

At the root: `tests/` holds tests and fixtures; `benchmark/` measurements; `deploy/` container configuration.

## Limits and non-goals

- The page reader uses the PDF text layer, without OCR. Hidden text, overlays
  and image content can differ from extracted text. Scans are not compared;
  text comparison and page rendering stop at 30 pages.
- Label vocabulary is finite. Missing dates and conflicting evidence can make
  a result inconclusive. Allowances and charges are not compared line by line;
  a wrong value equal to another invoice value can escape detection.
- Comparison rules are this project's own, not official validation rules.
  Agreement does not prove authenticity or authorise payment. There is no
  supplier matching, purchase-order matching or tax-advice function.
- Use one server process. Inboxes, review events and limits are in memory and
  disappear on restart. Public mode uploads documents to the hosting server.
- Plug-ins are trusted Python code loaded into that process, without a sandbox.

## Deployment

[deploy/README.md](deploy/README.md) describes the single-server Docker setup,
HTTPS through Caddy or Cloudflare Tunnel, public-mode limits and maintenance.
Use test invoices for a public demo.

## Bundled third-party artefacts

- CEN EN 16931 CII rules: EUPL-1.2; [licence and source](src/receipt_workbench/validation/en16931-cii-1.3.16/).
- KoSIT XRechnung CII rules: Apache-2.0, with the upstream CEN/DIN notice; [licence and source](src/receipt_workbench/validation/xrechnung-3.0.2-schematron-2.6.0/).
- Compact ICC Profiles `sRGB-v2-magic.icc`: CC0-1.0; [licence](src/receipt_workbench/testset/icc-LICENSE.txt).
- Vendored Torph animation module: MIT; [licence](src/receipt_workbench/review/static/vendor/torph.LICENSE.txt).

Python dependencies carry their own licences. Generated PDFs embed ReportLab's
Bitstream Vera fonts under the Bitstream Vera licence; the fonts come from the
dependency. No licence for the project's own code has been selected yet.
