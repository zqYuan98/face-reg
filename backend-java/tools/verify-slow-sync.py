"""Loopback-only slow-reader probe against synthetic data. Leaves people unchanged."""
import concurrent.futures
import http.cookiejar
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

base = os.environ.get('FACE_VERIFY_URL', 'http://127.0.0.1:28080')
address = urllib.parse.urlsplit(base)
if address.hostname != '127.0.0.1' or address.scheme != 'http':
    raise SystemExit('Only local synthetic HTTP validation is permitted')
device_token = os.environ['FACE_VERIFY_DEVICE_TOKEN']
password = os.environ['FACE_VERIFY_PASSWORD']
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def admin(path, payload=None, csrf=None):
    headers = {'Content-Type': 'application/json'}
    if csrf: headers[csrf['headerName']] = csrf['token']
    req = urllib.request.Request(base + path, headers=headers, data=json.dumps(payload).encode() if payload else None)
    with opener.open(req, timeout=15) as response: return json.load(response)

csrf = admin('/api/csrf')['data']
admin('/api/login', {'username': 'admin', 'password': password}, csrf)
cookie = '; '.join(f'{c.name}={c.value}' for c in jar)

def page(_):
    start = time.perf_counter()
    with urllib.request.urlopen(urllib.request.Request(base + '/api/users/page?page=1&size=20', headers={'Cookie': cookie}), timeout=10) as response:
        data = json.load(response)['data']
        assert len(data['records']) <= 20
        return round((time.perf_counter()-start)*1000, 3)

def slow_reader():
    connection = socket.socket()
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    connection.settimeout(90)
    connection.connect((address.hostname, address.port))
    connection.sendall(('GET /api/sync/users HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer ' + device_token + '\r\nConnection: close\r\n\r\n').encode())
    headers = b''
    while not headers.endswith(b'\r\n\r\n'):
        part = connection.recv(1)
        if not part: raise AssertionError('Closed before headers')
        headers += part
    assert headers.startswith(b'HTTP/1.1 200'), headers[:100]
    return connection

connections = []
try:
    # First request may generate the complete file; its deadline starts after generation.
    connections.append(slow_reader()); first_started = time.perf_counter()
    connections.append(slow_reader()); second_started = time.perf_counter()
    try:
        urllib.request.urlopen(urllib.request.Request(base + '/api/sync/users', headers={'Authorization': 'Bearer ' + device_token}), timeout=10)
        raise AssertionError('Third download must be rejected while both slots are held')
    except urllib.error.HTTPError as error: assert error.code == 429
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as workers:
        durations = sorted(workers.map(page, range(50)))
    print(json.dumps({'heldDownloads': 2, 'thirdStatus': 429, 'adminRequests': 50, 'adminP95Ms': durations[47]}), flush=True)
    # Periodic small reads model a trickle reader; a total deadline must not restart on progress.
    while time.perf_counter()-second_started < 64:
        for connection in connections: connection.recv(32)
        time.sleep(2)
    remaining = []
    for connection in connections:
        connection.settimeout(4)
        count = 0
        while chunk := connection.recv(65536):
            count += len(chunk)
            assert count < 32*1024*1024, 'Deadline failed: server continues sending the large snapshot'
        remaining.append(count)
    # A new request is accepted after both expired downloads released their leases.
    recovered = slow_reader(); recovered.close()
    report = {'heldDownloads': 2, 'thirdStatus': 429, 'adminRequests': 50, 'adminP95Ms': durations[47],
              'closedAfterTotalDeadline': True, 'remainingBufferedBytes': remaining, 'postTimeoutStatus': 200,
              'elapsedSeconds': round(time.perf_counter()-first_started, 3)}
    Path(os.environ.get('FACE_VERIFY_OUTPUT', 'slow-sync-result.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
finally:
    for connection in connections: connection.close()
