"""Concurrent load against /api/extract_feature: throughput, latency percentiles, status counts.

    python tools/load_test.py --url http://127.0.0.1:8000 --images "D:/lfw/*/*.jpg" --concurrency 8 --requests 400

Images are sent as stored (JPEG bytes); every request reuses the image list round-robin.
"""
import argparse
import asyncio
import collections
import glob
import time

import httpx


async def run(urls, payloads, concurrency, total):
    latencies, statuses = [], collections.Counter()
    counter = iter(range(total))

    async def client_loop(client):
        for i in counter:
            body = payloads[i % len(payloads)]
            url = urls[i % len(urls)]
            started = time.perf_counter()
            try:
                response = await client.post(f"{url}/api/extract_feature", files={"image": ("face.jpg", body)})
                statuses[response.status_code] += 1
                if response.status_code == 200:
                    latencies.append((time.perf_counter() - started) * 1000)
            except httpx.HTTPError as error:
                statuses[type(error).__name__] += 1

    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=concurrency)) as client:
        started = time.perf_counter()
        await asyncio.gather(*(client_loop(client) for _ in range(concurrency)))
        elapsed = time.perf_counter() - started
    latencies.sort()
    pick = lambda q: latencies[min(len(latencies) - 1, int(len(latencies) * q))] if latencies else float("nan")
    print(f"concurrency={concurrency} requests={total} elapsed={elapsed:.1f}s "
          f"ok/s={len(latencies) / elapsed:.1f} p50={pick(0.5):.0f}ms p95={pick(0.95):.0f}ms "
          f"p99={pick(0.99):.0f}ms statuses={dict(statuses)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="comma-separated; requests rotate over them")
    parser.add_argument("--images", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--requests", type=int, default=400)
    args = parser.parse_args()
    payloads = [open(p, "rb").read() for p in sorted(glob.glob(args.images))[: args.limit]]
    if not payloads:
        raise SystemExit("no images matched")
    urls = [u.strip().rstrip("/") for u in args.url.split(",") if u.strip()]
    asyncio.run(run(urls, payloads, args.concurrency, args.requests))


if __name__ == "__main__":
    main()
