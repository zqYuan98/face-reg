"""Read-only loopback capacity probe. Seed face_test separately; no production URL accepted."""
import concurrent.futures
import http.cookiejar
import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

base = os.environ.get("FACE_VERIFY_URL", "http://127.0.0.1:28080")
if urllib.parse.urlsplit(base).hostname != "127.0.0.1":
    raise SystemExit("This synthetic probe only permits loopback")
password = os.environ["FACE_VERIFY_PASSWORD"]
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def initial(path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers[token["headerName"]] = token["token"]
    with opener.open(urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload else None, headers=headers), timeout=15) as response:
        return json.load(response)

csrf = initial("/api/csrf")["data"]
initial("/api/login", {"username": "admin", "password": password}, csrf)
cookie = "; ".join(f"{c.name}={c.value}" for c in jar)

def request(path):
    started = time.perf_counter()
    req = urllib.request.Request(base + path, headers={"Cookie": cookie})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read(); status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read(); status = error.code
    result = json.loads(raw)
    records = result.get("data", {}).get("records", []) if isinstance(result.get("data"), dict) else []
    if any("featureData" in row for row in records):
        raise AssertionError("Management page leaked feature data")
    return {"ms": round((time.perf_counter()-started)*1000, 3), "bytes": len(raw), "status": status,
            "rows": len(records), "total": result.get("data", {}).get("total") if isinstance(result.get("data"),dict) else None}

scenarios = {
    "first_page": "/api/users/page?page=1&size=20",
    "group_page": "/api/users/page?page=1&size=50&groupId=1",
    "contains_filter": "/api/users/page?page=1&size=20&name=000",
    "deep_page": "/api/users/page?page=5000&size=20",
}
report = {"dataset": "100000 synthetic people, 1024 floats each, 200 groups", "concurrency": 20, "requestsPerScenario": 100, "scenarios": {}}
for name, path in scenarios.items():
    for _ in range(3): request(path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as workers:
        results = list(workers.map(request, [path]*100))
    durations = sorted(r["ms"] for r in results)
    report["scenarios"][name] = {"p50_ms": statistics.median(durations), "p95_ms": durations[94], "p99_ms": durations[98],
        "errors": sum(r["status"] != 200 for r in results), "maxRows": max(r["rows"] for r in results),
        "maxBytes": max(r["bytes"] for r in results), "total": results[-1]["total"]}
report["legacy_cap"] = request("/api/users/list")
report["invalid_size"] = request("/api/users/page?size=100001")
assert report["legacy_cap"]["status"] == 409
assert report["invalid_size"]["status"] == 400
assert report["scenarios"]["first_page"]["total"] == 100000
assert report["scenarios"]["first_page"]["maxRows"] == 20
output = Path(os.environ.get("FACE_VERIFY_OUTPUT", "capacity-result.json"))
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
