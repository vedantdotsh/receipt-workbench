# Extending Receipt Workbench

## Connect your own parts

Install a package declaring entry points in one of these groups in the same environment.
The built-ins use the same registry.

| Entry-point group | Contract | Built-ins |
|---|---|---|
| `receipt_workbench.invoice_parsers` | XML root to `Invoice`; declares supported `flavors` | `cii` |
| `receipt_workbench.page_readers` | PDF bytes to `PdfText` with positions | `pdf_text` |
| `receipt_workbench.validators` | XML, flavour, profile and guideline to `Layer` | `schema`, `rules`, `xrechnung` |
| `receipt_workbench.exports` | Evidence and review events to `Export` | `json` |

See [harness/protocols.py](../src/receipt_workbench/harness/protocols.py) for contracts.
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

