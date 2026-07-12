"""Structural and baseline checks for the versioned injection corpus."""

import json
from pathlib import Path

from jeli_scoped_mcp.security import InjectionDefense

CORPUS = Path(__file__).parent / "fixtures" / "injection_classifier_corpus.json"


def _cases() -> list[dict]:
    return json.loads(CORPUS.read_text())["cases"]


def test_injection_corpus_is_balanced_and_identifiable():
    cases = _cases()
    ids = [case["id"] for case in cases]
    labels = [case["label"] for case in cases]
    assert len(cases) == 32
    assert len(ids) == len(set(ids))
    assert labels.count(True) == labels.count(False) == 16
    assert all(case["category"] and case["text"].strip() for case in cases)


def test_regex_baseline_false_positives_are_explicit():
    benign = [case for case in _cases() if not case["label"]]
    false_positives = [
        case["id"] for case in benign if InjectionDefense.is_instruction_like(case["text"])
    ]
    # A direct quotation of the canonical marker remains a Layer-1 hit. The
    # corpus keeps it as a hard negative so Layer-2 quality is not overstated.
    assert false_positives == ["benign-04"]


def test_corpus_contains_regex_clean_attacks_for_layer_two():
    attacks = [case for case in _cases() if case["label"]]
    regex_clean = [
        case["id"] for case in attacks if not InjectionDefense.is_instruction_like(case["text"])
    ]
    assert len(regex_clean) >= 8
