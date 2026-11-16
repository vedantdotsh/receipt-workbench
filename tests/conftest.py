"""Shared synthetic invoice builders and finding lookups."""

import copy

import pytest

from receipt_workbench.testset import synth


def hybrid(base=0, layout="A", mutate=None, side="pdf"):
    spec = synth.build_spec(synth.BASES[base])
    changed = copy.deepcopy(spec)
    if mutate:
        mutate(changed, 1)
    xml_spec, pdf_spec = (changed, spec) if side == "xml" else (spec, changed)
    return synth.make_hybrid(xml_spec, pdf_spec, layout)


def findings(result):
    return {f["key"]: f for f in result["comparison"]["findings"]}


@pytest.fixture(name="hybrid")
def hybrid_factory():
    return hybrid


@pytest.fixture(name="findings")
def findings_factory():
    return findings
