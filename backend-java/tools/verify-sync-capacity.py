"""Loopback-only synthetic sync probe; does not change people, but creates/releases a device lease."""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get('FACE_VERIFY_URL', 'http://127.0.0.1:28080')
if urllib.parse.urlsplit(BASE).hostname != '127.0.0.1':
    raise SystemExit('Synthetic probe only permits loopback')
TOKEN = os.environ['FACE_VERIFY_DEVICE_TOKEN']
EXPECTED = int(os.environ.get('FACE_VERIFY_EXPECTED_ROWS', '100000'))
HEADERS = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}

def request(path, payload=None, post=False):
    req = urllib.request.Request(BASE + path, headers=HEADERS,
        data=json.dumps(payload).encode() if payload is not None else (b'' if post else None))
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)['data']

def legacy():
    start = time.perf_counter()
    req = urllib.request.Request(BASE + '/api/sync/users?lastSyncTime=9999999999999', headers=HEADERS)
    with urllib.request.urlopen(req, timeout=90) as response:
        first = time.perf_counter()
        decoder = json.JSONDecoder()
        buffer = ''
        import codecs
        utf8 = codecs.getincrementaldecoder('utf-8')()
        in_data = False; done = False; rows = 0; size = 0; max_buffer = 0
        digest = hashlib.sha256()
        while chunk := response.read(65536):
            size += len(chunk); digest.update(chunk); buffer += utf8.decode(chunk)
            max_buffer = max(max_buffer, len(buffer))
            if not in_data:
                position = buffer.find('"data":[')
                if position < 0: continue
                buffer = buffer[position + 8:]; in_data = True
            while not done:
                buffer = buffer.lstrip(' \r\n\t,')
                if buffer.startswith(']'):
                    done = True; buffer = buffer[1:]; break
                try: record, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError: break
                assert len(json.loads(record['featureData'])) == 1024
                rows += 1; buffer = buffer[end:]
        buffer += utf8.decode(b'', final=True)
        assert done and buffer.strip() == '}' and rows == EXPECTED
        assert size == int(response.headers['Content-Length'])
        return {'rows': rows, 'bytes': size, 'sha256': digest.hexdigest(),
                'generation_ttfb_seconds': round(first-start, 3),
                'total_seconds': round(time.perf_counter()-start, 3),
                'client_max_parse_buffer_chars': max_buffer,
                'revision': response.headers['X-Sync-Revision']}

report = {'expectedRows': EXPECTED}
report['v1_first'] = legacy()
print(json.dumps({'v1_first': report['v1_first']}), flush=True)
report['v1_cached'] = legacy()
assert report['v1_first']['sha256'] == report['v1_cached']['sha256']
started = time.perf_counter()
lease = request('/api/sync/v2/bootstrap', post=True)
after = 0; rows = 0; pages = 0; max_rows = 0
while True:
    page = request('/api/sync/v2/bootstrap/' + lease['leaseId'] + '/page?' + urllib.parse.urlencode({'afterId': after, 'limit': 200}))
    records = page['records']; rows += len(records); pages += 1; max_rows = max(max_rows, len(records))
    assert len(records) <= 200
    assert all(record['id'] > after and len(json.loads(record['featureData'])) == 1024 for record in records)
    after = page['nextAfterId']
    if not page['hasMore']: break
assert rows == EXPECTED
upper = request('/api/sync/v2/bootstrap/' + lease['leaseId'] + '/finish', post=True)
cursor = lease['startRevision']; change_pages = 0
while True:
    changes = request('/api/sync/v2/changes?' + urllib.parse.urlencode({'epoch': lease['epoch'], 'scopeVersion': lease['scopeVersion'],
        'afterRevision': cursor, 'untilRevision': upper['upperRevision'], 'leaseId': lease['leaseId'], 'limit': 200}))
    cursor = changes['nextRevision']; change_pages += 1
    if not changes['hasMore']: break
ack = request('/api/sync/v2/ack', {'epoch': lease['epoch'], 'scopeVersion': lease['scopeVersion'], 'revision': cursor, 'leaseId': lease['leaseId']})
assert ack['ackRevision'] == upper['upperRevision']
report['v2'] = {'rows': rows, 'pages': pages, 'maxRows': max_rows, 'changesPages': change_pages,
                'ackedRevision': cursor, 'total_seconds': round(time.perf_counter()-started, 3)}
Path(os.environ.get('FACE_VERIFY_OUTPUT', 'sync-capacity-result.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
