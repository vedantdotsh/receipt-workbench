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
