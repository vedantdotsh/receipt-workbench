Test set: 444 files from 8 base invoices x 4 layouts (32 clean, 412 with one planted difference).

- Clean invoices: 0 false alarms and 0 inconclusive out of 32 (naive baseline: 0 false alarms).
- Planted differences: 381 of 412 detected on the right field, 0 flagged on another field, 31 reported as inconclusive, 0 missed. Naive baseline: 224 of 412.
- Clean-file field coverage: 571 of 716 fields confirmed at their label, 109 found on the page without a label, 36 not located.
- 412 of 412 files with a planted difference still pass schema and rule validation.

| Planted difference | Files | Detected | Other field | Inconclusive | Missed | Naive baseline |
|---|---|---|---|---|---|---|
| bic (pdf changed) | 32 | 32 | 0 | 0 | 0 | 0 |
| due_date (pdf changed) | 28 | 21 | 0 | 7 | 0 | 0 |
| extra_line (pdf changed) | 32 | 32 | 0 | 0 | 0 | 32 |
| extra_line (xml changed) | 32 | 32 | 0 | 0 | 0 | 32 |
| gross_total (pdf changed) | 32 | 24 | 0 | 8 | 0 | 32 |
| iban (pdf changed) | 32 | 32 | 0 | 0 | 0 | 32 |
| iban (xml changed) | 32 | 32 | 0 | 0 | 0 | 32 |
| invoice_number (pdf changed) | 32 | 24 | 0 | 8 | 0 | 32 |
| issue_date (xml changed) | 32 | 24 | 0 | 8 | 0 | 0 |
| line_amount (pdf changed) | 32 | 32 | 0 | 0 | 0 | 0 |
| line_quantity_recomputed (xml changed) | 32 | 32 | 0 | 0 | 0 | 32 |
| seller_vat_id (pdf changed) | 32 | 32 | 0 | 0 | 0 | 0 |
| tax_amount (pdf changed) | 32 | 32 | 0 | 0 | 0 | 0 |

| Layout | Files | Detected | Other field | Inconclusive | Missed |
|---|---|---|---|---|---|
| A | 103 | 103 | 0 | 0 | 0 |
| B | 103 | 103 | 0 | 0 | 0 |
| C | 103 | 103 | 0 | 0 | 0 |
| D | 103 | 72 | 0 | 31 | 0 |

## Third-party validity checks (16 November 2026)

The same 444 files were run through Mustangproject's hybrid-invoice validator and veraPDF's
PDF/A-3b validator. These runs measure whether the tested configurations flag the planted
differences, not every page/data check either tool may support. The files are synthetic
and share an author with the checker.

| Tool | Version | Clean accepted / 32 | Changed: flagged on the changed field / 412 | Changed: rejected for another reason | Changed: not flagged | Errors |
|---|---|---|---|---|---|---|
| Mustangproject | 2.26.0 | 32 (overall verdict) | 0 | 0 | 412 | 0 |
| veraPDF (PDF/A-3b) | 1.30.4 | 32 | 0 | 0 | 412 | 0 |

**Clean files.** Both validators accept all 32 clean and all 412 changed files.
Mustangproject reports valid overall, XML and nested PDF verdicts throughout. The generated
PDFs embed ReportLab's Bitstream Vera regular/bold fonts (Bitstream Vera licence) and declare
the bundled `sRGB-v2-magic.icc` from [Compact ICC Profiles](https://github.com/saucecontrol/Compact-ICC-Profiles)
(CC0-1.0); all drawing colours are RGB.
Mustangproject runs veraPDF internally, so the two PDF verdicts are not independent. Neither
tool flags the planted page/data differences.

**How a result is counted.** A changed file counts as flagged on the changed field only if
the tool's overall verdict is invalid and it reports a blocking rule on the field that was
changed, which its clean counterpart does not report. An invalid verdict for other reasons is
"another reason". A timeout, crash or unreadable report is an error, never "not flagged".

**Reproduce.** The tools are not part of the repository. Download URLs and SHA-256 checksums
are in `DOWNLOADS` in `src/receipt_workbench/testset/third_party.py`: the Mustang CLI JAR
(Apache-2.0), the veraPDF installer (GPL-3.0-or-later or MPL-2.0) and, if no Java is installed,
a Temurin 21 JRE. Put them in `.tools/`, install the veraPDF CLI to `.tools/verapdf`, then:

```
uv run receipt-workbench make-testset --out testset
uv run receipt-workbench bench-third-party --dir testset --jobs 4
```

The run took about 15 minutes with four parallel jobs. Per-file results are written to
`benchmark/third-party-results.json`, which is not committed; raw reports go beside the output
under `reports/`, leaving the tool cache read-only.
