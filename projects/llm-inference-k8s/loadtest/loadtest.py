"""Closed-loop load test for an OpenAI-compatible endpoint.

Reports what matters for LLM serving: time to first token, end-to-end latency,
and aggregate completion tokens per second at a given concurrency.

    python loadtest.py --url http://localhost:8000 --concurrency 4 --requests 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx

PROMPT = "Explain in three sentences why continuous batching improves GPU throughput."


async def one_request(client: httpx.AsyncClient, model: str, max_tokens: int) -> dict:
    payload = {
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": PROMPT}],
    }
    started = time.perf_counter()
    ttft, tokens = None, 0
    async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
        if response.status_code != 200:
            await response.aread()
            return {"ok": False, "status": response.status_code}
        async for line in response.aiter_lines():
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            if ttft is None:
                ttft = time.perf_counter() - started
            usage = json.loads(line[5:]).get("usage")
            if usage:
                tokens = usage.get("completion_tokens", 0)
    return {"ok": True, "ttft": ttft, "total": time.perf_counter() - started, "tokens": tokens}


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--model", default="default")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=128)
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(args.requests):
        queue.put_nowait(i)
    results: list[dict] = []

    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=300) as client:

        async def worker() -> None:
            while not queue.empty():
                queue.get_nowait()
                results.append(await one_request(client, args.model, args.max_tokens))

        wall_start = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(args.concurrency)))
        wall = time.perf_counter() - wall_start

    ok = [r for r in results if r["ok"]]
    print(f"requests: {len(results)}  ok: {len(ok)}  failed: {len(results) - len(ok)}")
    if not ok:
        return
    ttfts = [r["ttft"] for r in ok if r["ttft"] is not None]
    totals = [r["total"] for r in ok]
    print(f"ttft   p50 {statistics.median(ttfts):.2f}s   p95 {percentile(ttfts, 95):.2f}s")
    print(f"total  p50 {statistics.median(totals):.2f}s   p95 {percentile(totals, 95):.2f}s")
    print(f"throughput: {sum(r['tokens'] for r in ok) / wall:.1f} completion tokens/s")


if __name__ == "__main__":
    asyncio.run(main())
