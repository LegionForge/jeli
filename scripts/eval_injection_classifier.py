#!/usr/bin/env python3
"""Measure Jeli's production Layer-2 classifier against the labeled corpus."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from jeli_scoped_mcp.config import Settings
from jeli_scoped_mcp.security import InjectionDefense


def _metrics(expected: list[bool], predicted: list[bool]) -> dict:
    tp = sum(want and got for want, got in zip(expected, predicted, strict=True))
    tn = sum(not want and not got for want, got in zip(expected, predicted, strict=True))
    fp = sum(not want and got for want, got in zip(expected, predicted, strict=True))
    fn = sum(want and not got for want, got in zip(expected, predicted, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "accuracy": round((tp + tn) / len(expected), 4),
    }


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    model = args.model or settings.reranker_model
    api_base = args.api_base or settings.litellm_base_url
    api_key = args.api_key or settings.litellm_api_key
    if not api_base:
        raise SystemExit("LITELLM_BASE_URL or --api-base is required")

    corpus = json.loads(args.corpus.read_text())
    cases = corpus["cases"]
    predicted: list[bool] = []
    durations: list[float] = []
    for case in cases:
        started = time.monotonic()
        verdict = await InjectionDefense.llm_classify_injection(
            case["text"],
            model=model,
            timeout=args.timeout,
            api_base=api_base,
            api_key=api_key,
        )
        durations.append(time.monotonic() - started)
        predicted.append(verdict)

    expected = [bool(case["label"]) for case in cases]
    report = {
        "provider": "LiteLLM proxy",
        "model": model,
        "corpus_schema": corpus["schema_version"],
        "cases": len(cases),
        "metrics": _metrics(expected, predicted),
        "latency_ms": {
            "mean": round(sum(durations) * 1000 / len(durations), 1),
            "max": round(max(durations) * 1000, 1),
        },
        "false_positives": [
            case["id"]
            for case, want, got in zip(cases, expected, predicted, strict=True)
            if not want and got
        ],
        "false_negatives": [
            case["id"]
            for case, want, got in zip(cases, expected, predicted, strict=True)
            if want and not got
        ],
    }
    print(json.dumps(report, indent=2))
    return int(
        report["metrics"]["precision"] < args.min_precision
        or report["metrics"]["recall"] < args.min_recall
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path("tests/fixtures/injection_classifier_corpus.json"),
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--min-precision", type=float, default=0.8)
    parser.add_argument("--min-recall", type=float, default=0.8)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
