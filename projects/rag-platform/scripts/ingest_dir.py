"""Ingest every Markdown or text file in a directory through the HTTP API.

python scripts/ingest_dir.py eval/corpus --url http://localhost:8080
"""

from __future__ import annotations

import argparse
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk ingest a directory")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--url", default="http://localhost:8080")
    args = parser.parse_args()

    files = sorted(p for p in args.directory.rglob("*") if p.suffix in {".md", ".txt"})
    with httpx.Client(base_url=args.url, timeout=60) as client:
        for path in files:
            response = client.post(
                "/ingest",
                json={"source": str(path.relative_to(args.directory)), "text": path.read_text()},
            )
            response.raise_for_status()
            print(f"{path.name}: {response.json()['chunks']} chunks")


if __name__ == "__main__":
    main()
