"""Run published validation artefacts against the XML. This module writes no rules.

Three layers, reported separately:
  schema  XSD for the detected profile (factur-x library's bundled Factur-X 1.0.x XSDs)
  rules   CEN EN 16931 CII Schematron for XRechnung, otherwise the library's
          Factur-X Schematron, executed here with Saxon-HE (saxonche)
  xrechnung  KoSIT's CII Schematron for invoices declaring XRechnung in BT-24

Outcomes are `passed`, `failed`, `not_run` (unsupported input) or `error` (the
validator itself broke). `not_run` and `error` are never shown as a pass.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import queue
import threading
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from lxml import etree

SVRL = "{http://purl.oclc.org/dsdl/svrl}"
LEVELS = {"MINIMUM": "minimum", "BASIC WL": "basicwl", "BASIC": "basic", "EN 16931": "en16931",
          "EXTENDED": "extended", "XRECHNUNG": "en16931"}
SCHEMATRON_FILES = {
    "minimum": "facturx-minimum/Factur-X_1.09_MINIMUM.xsl",
    "basicwl": "facturx-basicwl/FACTUR-X_BASIC-WL.xslt",
    "basic": "facturx-basic/Factur-X_1.09_BASIC.xsl",
    "en16931": "facturx-en16931/FACTUR-X_EN16931.xslt",
    "extended": "facturx-extended/FACTUR-X_EXTENDED.xslt",
}
XRECHNUNG_ARTEFACT = "xrechnung-3.0.2-schematron-2.6.0/XRechnung-CII-validation.xsl"
EN16931_ARTEFACT = "en16931-cii-1.3.16/EN16931-CII-validation.xslt"


@dataclass
class RuleResult:
    rule_id: str
    severity: str  # error | warning
    message: str
    location: str | None = None


@dataclass
class Layer:
    name: str
    outcome: str  # passed | failed | not_run | error
    engine: str
    artefact: str | None = None
    artefact_sha256: str | None = None
    detail: str | None = None
    results: list[RuleResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _artefact_dir() -> str:
    import facturx

    return os.path.join(os.path.dirname(facturx.__file__), "xsd_and_schematron")


@lru_cache(maxsize=None)
def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@lru_cache(maxsize=1)
def _saxon():
    from saxonche import PySaxonProcessor

    return PySaxonProcessor(license=False)


@lru_cache(maxsize=None)
def _compiled(path: str):
    return _saxon().new_xslt30_processor().compile_stylesheet(stylesheet_file=path)


def _lib_version() -> str:
    from importlib.metadata import version

    return version("factur-x")


def validate_schema(xml: bytes, flavor: str, profile: str) -> Layer:
    engine = f"factur-x {_lib_version()} (lxml XSD)"
    if flavor == "zugferd1":
        fx_flavor, level = "zugferd", "autodetect"
    elif flavor == "cii" and profile in LEVELS:
        fx_flavor, level = "factur-x", LEVELS[profile]
    else:
        return Layer("schema", "not_run", engine, detail=f"No schema configured for flavour '{flavor}', profile '{profile}'.")
    import facturx

    try:
        facturx.xml_check_xsd(xml, flavor=fx_flavor, level=level)
        return Layer("schema", "passed", engine, artefact=f"{fx_flavor}/{level}")
    except Exception as e:
        msg = str(e)
        if "not valid against the official XML Schema" in msg or "XSD" in msg or "Schema" in msg:
            return Layer("schema", "failed", engine, artefact=f"{fx_flavor}/{level}",
                         results=[RuleResult("XSD", "error", msg[:1200])])
        return Layer("schema", "error", engine, detail=msg[:600])


# SaxonC runs in a native isolate bound to the thread that created it. A web server calls
# us from changing worker threads, so every Saxon call goes through one dedicated thread.
# It is a daemon thread with its own queue (not an executor) so that it is still alive when
# atexit runs: the compiled stylesheets and the processor must be freed on that same thread,
# in that order, or the process crashes on the way out.
class _SaxonThread:
    def __init__(self):
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _loop(self):
        while True:
            fn, args, done = self._jobs.get()
            try:
                done.set_result(fn(*args))
            except BaseException as e:  # noqa: BLE001 - handed back to the caller
                done.set_exception(e)

    def call(self, fn, *args, timeout=60):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name="saxon", daemon=True)
                self._thread.start()
                atexit.register(self._release)
        done: Future = Future()
        self._jobs.put((fn, args, done))
        return done.result(timeout=timeout)

    def _release(self):
        def free():
            import gc

            _compiled.cache_clear()
            gc.collect()
            _saxon.cache_clear()
            gc.collect()

        try:
            self.call(free, timeout=10)
        except Exception:
            pass


_SAXON_THREAD = _SaxonThread()


def validate_rules(xml: bytes, flavor: str, profile: str, guideline: str) -> Layer:
    try:
        return _SAXON_THREAD.call(_rules_in_saxon_thread, xml, flavor, profile, guideline)
    except Exception as e:
        return Layer("rules", "error", "Saxon-HE via saxonche", detail=f"{type(e).__name__}: {e}"[:600])


def validate_xrechnung(xml: bytes, flavor: str, guideline: str) -> Layer:
    engine = "Saxon-HE via saxonche"
    if flavor != "cii" or "xrechnung" not in guideline.lower():
        return Layer("xrechnung", "not_run", engine, detail="Not run: not an XRechnung CII invoice (BT-24).")
    path = os.path.join(os.path.dirname(__file__), *XRECHNUNG_ARTEFACT.split("/"))
    try:
        return _SAXON_THREAD.call(_schematron_in_saxon_thread, xml, "xrechnung", path, XRECHNUNG_ARTEFACT)
    except Exception as error:  # noqa: BLE001 - validator failures must remain evidence, not crash the check
        return Layer("xrechnung", "error", engine, artefact=XRECHNUNG_ARTEFACT,
                     detail=f"{type(error).__name__}: {error}"[:600])


def _rules_in_saxon_thread(xml: bytes, flavor: str, profile: str, guideline: str) -> Layer:
    if flavor == "cii" and "xrechnung" in guideline.lower():
        path = os.path.join(os.path.dirname(__file__), *EN16931_ARTEFACT.split("/"))
        return _schematron_in_saxon_thread(xml, "rules", path, EN16931_ARTEFACT)
    if flavor != "cii" or profile not in LEVELS:
        return Layer("rules", "not_run", "Saxon-HE",
                     detail=f"No Schematron configured for flavour '{flavor}', profile '{profile}'.")
    rel = SCHEMATRON_FILES[LEVELS[profile]]
    path = os.path.join(_artefact_dir(), *rel.split("/"))
    return _schematron_in_saxon_thread(xml, "rules", path, rel)


def _schematron_in_saxon_thread(xml: bytes, name: str, path: str, artefact: str) -> Layer:
    try:
        engine = f"{_saxon().version} via saxonche"
        exe = _compiled(path)
        node = _saxon().parse_xml(xml_text=xml.decode("utf-8-sig"))
        svrl = exe.transform_to_string(xdm_node=node)
        root = etree.fromstring(svrl.encode("utf-8"))
    except Exception as e:
        return Layer(name, "error", "Saxon-HE via saxonche", artefact=artefact, detail=str(e)[:600])
    results = []
    for el in root.iter(SVRL + "failed-assert", SVRL + "successful-report"):
        text = " ".join(" ".join("".join(t.itertext()).split()) for t in el.iter(SVRL + "text"))
        flag = (el.get("flag") or el.get("role") or "error").lower()
        rid = el.get("id") or ""
        if not rid and text.startswith("["):
            rid = text[1:text.find("]")]
        results.append(RuleResult(rid or "rule", "warning" if flag.startswith("warn") else "error", text[:800], el.get("location")))
    failed = any(r.severity == "error" for r in results)
    return Layer(name, "failed" if failed else "passed", engine, artefact=artefact, artefact_sha256=_sha(path),
                 results=results)
