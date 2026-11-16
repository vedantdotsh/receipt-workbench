import json
from pathlib import Path

from ..harness.protocols import Export


def export_json(result: dict, review_events: list[dict]) -> Export:
    bundle = {"kind": "receipt-workbench evidence record", "result": result, "review_events": review_events,
              "disclaimer": "Technical consistency check of the PDF text layer against the embedded XML. "
                            "Not tax advice, not a fraud check, not a payment approval."}
    data = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return Export(data, "application/json", Path(result["file"]["name"]).stem + ".evidence.json")
