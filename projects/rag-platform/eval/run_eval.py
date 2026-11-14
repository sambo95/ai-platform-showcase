"""Retrieval quality gate.

Ingests eval/corpus, runs every question in eval/golden.jsonl, and reports
recall@k and MRR. Exits non-zero when either falls below its threshold, so CI
blocks a change to chunking or embeddings that makes retrieval worse.

    python -m eval.run_eval --k 3 --min-recall 0.9 --min-mrr 0.8
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import Settings
from app.pipeline import RagPipeline

ROOT = Path(__file__).parent


def evaluate(pipeline: RagPipeline, k: int) -> dict:
    for path in sorted((ROOT / "corpus").glob("*.md")):
        pipeline.ingest(path.name, path.read_text())
    cases = [json.loads(line) for line in (ROOT / "golden.jsonl").read_text().splitlines() if line]
    hits_at_k, reciprocal_ranks, misses = 0, 0.0, []
    for case in cases:
        sources = [hit.source for hit in pipeline.retrieve(case["question"], k)]
        if case["source"] in sources:
            hits_at_k += 1
            reciprocal_ranks += 1.0 / (sources.index(case["source"]) + 1)
        else:
            misses.append(
                {"question": case["question"], "expected": case["source"], "got": sources}
            )
    return {
        "cases": len(cases),
        "k": k,
        "recall_at_k": hits_at_k / len(cases),
        "mrr": reciprocal_ranks / len(cases),
        "misses": misses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval quality gate")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--min-recall", type=float, default=0.9)
    parser.add_argument("--min-mrr", type=float, default=0.8)
    args = parser.parse_args()

    report = evaluate(RagPipeline(Settings(qdrant_url=":memory:")), args.k)
    print(json.dumps(report, indent=2))
    passed = report["recall_at_k"] >= args.min_recall and report["mrr"] >= args.min_mrr
    print("PASS" if passed else "FAIL", file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
