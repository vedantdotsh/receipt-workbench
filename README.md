# Receipt Workbench

A German hybrid e-invoice (ZUGFeRD, Factur-X) is a PDF with a second, machine-readable
copy of the invoice inside it. A person approves the page. The accounting system books the
embedded XML. Nothing forces the two to agree.

Receipt Workbench checks one file at a time and answers four separate questions:

1. **Is the embedded data valid?** It runs the published XSD and Schematron rules.
2. **Does the page say the same as the data?** Field by field, including invoice lines and
   the VAT breakdown.
3. **Where is the evidence?** Every finding points at the place on the page and the XPath in
   the data.
4. **How much was actually checked?** Fields that could not be located are listed, and the
   verdict becomes "inconclusive" instead of a pass.

It is a portfolio prototype and a working name. It is a technical consistency check, not tax
advice, not a fraud check and not a payment approval.

## Try it

```
uv sync
uv run receipt-workbench serve          # review screen on http://127.0.0.1:8321
uv run receipt-workbench check FILE...  # command line, add -v for findings, --json for the record
uv run pytest
```

In the review screen, "Load sample invoices" builds six synthetic invoices on the spot: a
clean one, a changed bank account, a changed line amount, a credit note with two VAT rates,
an unfamiliar layout the tool cannot read confidently, and an XML with a rule error. Click
a row in the field table to see the spot on the page. The screen is in English and German.

Files are kept in memory and never leave the machine.

## What the comparison does

For each value in the XML it looks for the place where that field is printed, using labels
in German, English and some French, and reads the number, date or identifier next to it.

| Result | Meaning |
|---|---|
| Matches | The XML value is printed at that field's label (or is a checksum-typed value such as an IBAN). |
| On page, unlabelled | The value is on the page, but not tied to a label the tool knows. |
| Differs | The label shows another value, that value is not any other value of this invoice, and the XML value is nowhere on the page. |
| Unclear | Conflicting evidence. |
| Not located | Neither the value nor a labelled place for it was found. |

A value that merely appears somewhere on the page is never enough to confirm an amount, a
date or an invoice number. "No difference in the checked fields" requires the invoice
number, invoice date, totals and IBAN to be located; otherwise the verdict is "Inconclusive".

## Results so far (16 November 2026)

**Seeded test set.** `receipt-workbench make-testset` generates 444 hybrid invoices from 8
invented base invoices in 4 page layouts: 32 clean and 412 with one planted difference on
either the page or the data. `receipt-workbench bench` scores them. Full table:
[benchmark/RESULTS.md](benchmark/RESULTS.md).

- Clean invoices: 0 false alarms out of 32.
- Planted differences: 381 of 412 detected on the right field, 24 reported as inconclusive,
  7 missed (the tool said "no difference").
- All 412 changed files still pass schema and rule validation, so a validator alone finds none of them.
- A naive baseline ("is the invoice number, total and IBAN somewhere on the page?") finds 224 of 412.
- Layouts A to C: 309 of 309. Layout D uses labels the tool was deliberately not written
  for: 72 detected, 24 inconclusive, 7 missed. The 7 misses are all changed due dates.

Read these numbers with care. The generator and the checker have the same author, and
layouts A to C were used while developing the rules. They show the method works on text it
can read, not how it performs on arbitrary invoices. Layout D is the closer estimate of an
unseen layout.

**Public sample invoices.** On the 97 comparable PDFs in the "correct" folders of the
[ZUGFeRD corpus](https://github.com/ZUGFeRD/corpus) (Apache-2.0, many files are variants of
the same invoice), the tool reports 81 with no difference, 8 inconclusive and 8 with a
difference. I read the page text and XML of all 8, and each is a real disagreement between
page and data:

| File | Page | Embedded data |
|---|---|---|
| `MustangGnuaccountingBeispielRE-20201121_508_withBOM.pdf` | invoice RE-20190610/507 | invoice RE-20201121/508 |
| `sample_inofficial_20190125_atgp_factur-x_v_1_0.pdf` | a German invoice, 571,04 € | a different French invoice, 671.15 |
| `acme_invoice-42_ZUGFeRD.pdf` | invoice 42, 1.589,80 € | a different invoice, 119 |
| `MustangGnuaccountingBeispielRE-20150613_503.pdf` | total 406,82 € | total 571.04 |
| `MustangGnuaccountingBeispielRE-20151008_504.pdf`, `…_504new.pdf` | dates in 2015 | dates in 2014 |
| `MustangGnuaccountingBeispielRE-20140703_502.pdf` | BIC COBADEFFXXX, net line amounts | BIC COBADEFXXX, gross line amounts |
| `zugferd_2p1_BASIC-WL_Einfach.pdf` | IBAN starting DE20 | IBAN starting FR20 |

These are demonstration files, so this says nothing about how often real invoices disagree.
It does show the check finds differences nobody planted for it.

## Limits

- It reads the PDF **text layer**. Hidden text, text drawn as an image and overlays are not
  what a person sees. The page image is shown next to every finding for that reason. Scans
  are reported as "not compared".
- If both copies are changed the same way, they agree. Agreement does not prove an invoice
  is genuine.
- Label vocabulary is finite. On an unknown layout the honest result is "inconclusive".
- Only UN/CEFACT CII is parsed (ZUGFeRD 2.x, Factur-X, XRechnung in CII syntax; ZUGFeRD 1.0
  best effort). UBL is reported as unsupported.
- Validation uses the Factur-X 1.0.x XSD and Schematron shipped with the `factur-x`
  library, run with Saxon-HE. Invoices declaring XRechnung in BT-24 additionally run the
  bundled KoSIT XRechnung CII Schematron 2.6.0 (XRechnung 3.0.2, BR-DE/BR-DEX); other
  invoices report this layer as not run.
  Older ZUGFeRD 2.0 samples often fail these newer rules; that has not been analysed.
- Allowances and charges are not compared line by line. A page that prints a wrong total
  which happens to equal another amount of the same invoice is not flagged.
- Not built yet from stage 1: the AWS deployment, batch upload limits for public use, the
  comparison against other tools.

## Layout

```
src/receipt_workbench/
  __init__.py             package version
  cli.py                  command-line entry point
  common/normalize.py     shared parsing and formatting helpers
  intake/ingest.py        hash, classify, extract the embedded XML, refuse DOCTYPE and ambiguous attachments
  invoice_data/cii.py     XML -> values with business term (BT) and XPath, Decimal amounts
  page_text/pdftext.py    words with positions -> rows -> typed tokens (numbers, dates, IBANs, VAT IDs)
  comparison/compare.py   the field, line and VAT comparison, coverage and verdict
  validation/validate.py  XSD and Schematron runs with engine and artefact versions
  validation/xrechnung-3.0.2-schematron-2.6.0/  pinned KoSIT CII XSLT, upstream licence and source checksums
  pipeline/check.py       one file in, one evidence record out
  testset/synth.py         seeded test set generator
  testset/bench.py         scorer
  review/server.py        local review screen
  review/static/          the screen and vendored scripts
tests/
  conftest.py             shared synthetic invoice helpers and fixtures
  test_<feature>.py       one test file per feature folder
```

All sample parties, bank accounts and tax numbers in the generated invoices are invented.
