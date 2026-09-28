from __future__ import annotations

import argparse
import copy
import io
import json
from pathlib import Path
import socket
import sys
import tarfile
from types import SimpleNamespace

import pytest
import yaml

from scripts import deploy_presence_release as release


COMMIT = 'a' * 40


def item(identifier: str, *, owned: bool = False) -> dict:
    return {'Id': identifier, 'Image': 'sha256:image', 'State': {'Running': True, 'StartedAt': 'start'},
            'HostConfig': {}, 'Config': {'Image': 'old-image', 'Env': [], 'Labels': {
                release.REVISION_LABEL: COMMIT, release.OWNER_LABEL: release.API,
            } if owned else {}}}


@pytest.fixture
def deploy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> release.Release:
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    return release.Release(COMMIT)


def test_commit_must_be_full_unambiguous_lowercase_sha() -> None:
    assert release.full_commit(COMMIT) == COMMIT
    for invalid in ('a' * 12, '../escape', 'A' * 40, 'a' * 39 + 'x'):
        with pytest.raises(argparse.ArgumentTypeError):
            release.full_commit(invalid)


def test_port_probe_reuses_time_wait_without_enabling_reuseport(monkeypatch) -> None:
    options = []

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def setsockopt(self, level, option, value):
            options.append((level, option, value))

        def bind(self, address):
            if (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) not in options:
                raise OSError('TIME_WAIT')

    monkeypatch.setattr(release, 'sys', SimpleNamespace(platform='linux'), raising=False)
    monkeypatch.setattr(release.socket, 'socket', lambda *args: Probe())
    release.port_free('127.0.0.1', 18002)
    assert options == [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]


def test_port_probe_still_rejects_active_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        with pytest.raises(release.ReleaseError, match='occupied'):
            release.port_free('127.0.0.1', listener.getsockname()[1])


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux TCP TIME_WAIT semantics')
def test_linux_time_wait_does_not_block_port_probe() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        address = listener.getsockname()
        listener.listen()
        with socket.create_connection(address, timeout=2) as client:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2)
                connection.shutdown(socket.SHUT_WR)
                assert client.recv(1) == b''
                client.shutdown(socket.SHUT_WR)
                assert connection.recv(1) == b''
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as naive_probe:
        with pytest.raises(OSError):
            naive_probe.bind(address)
    release.port_free(*address)


def test_port_wait_retries_transient_release_but_never_ignores_active_listener(monkeypatch) -> None:
    attempts = []
    ticks = iter((0, 0, 10))
    monkeypatch.setattr(release.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)

    def busy(host, port):
        attempts.append((host, port))
        raise release.ReleaseError('occupied')

    monkeypatch.setattr(release, 'port_free', busy)
    with pytest.raises(release.ReleaseError, match='occupied'):
        release.wait_port_free('127.0.0.1', 18002, timeout=10)
    assert len(attempts) == 2


def test_port_wait_accepts_only_after_probe_succeeds(monkeypatch) -> None:
    attempts = []
    monkeypatch.setattr(release.time, 'monotonic', lambda: 0)
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)

    def transient(host, port):
        attempts.append((host, port))
        if len(attempts) == 1:
            raise release.ReleaseError('occupied')

    monkeypatch.setattr(release, 'port_free', transient)
    release.wait_port_free('127.0.0.1', 18002)
    assert len(attempts) == 2


def test_rolled_back_release_can_recreate_candidate_with_original_ids(deploy, monkeypatch) -> None:
    deploy.state = {'stage': 'rolled-back', 'config_sha256': 'config-hash',
                    'originals': {release.API: 'original-api', release.CONSOLE: 'original-console'}}
    containers = {release.API: item('original-api'), release.CONSOLE: item('original-console')}
    monkeypatch.setattr(release, 'inspect', containers.get)
    monkeypatch.setattr(release, 'sha256', lambda _: 'config-hash')
    monkeypatch.setattr(release, 'port_free', lambda *args: None)
    monkeypatch.setattr(release, 'wait_http', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, 'verify_images', lambda: None)
    starts = []
    monkeypatch.setattr(deploy, 'run_container', lambda *args, **kwargs: starts.append((args, kwargs)))
    deploy.candidate_start()
    assert deploy.state['stage'] == 'candidate'
    assert starts[0][0] == (deploy.candidate,)
    assert starts[0][1]['old_api']['Id'] == 'original-api'


@pytest.mark.parametrize('target', [release.API, release.CONSOLE])
def test_retry_after_rollback_rejects_changed_original_id_before_any_start(deploy, monkeypatch, target) -> None:
    deploy.state = {'stage': 'rolled-back', 'config_sha256': 'config-hash',
                    'originals': {release.API: 'original-api', release.CONSOLE: 'original-console'}}
    containers = {release.API: item('original-api'), release.CONSOLE: item('original-console')}
    containers[target]['Id'] = 'unrelated'
    monkeypatch.setattr(release, 'inspect', containers.get)
    monkeypatch.setattr(release, 'sha256', lambda _: 'config-hash')
    monkeypatch.setattr(deploy, 'verify_images', lambda: None)
    starts = []
    monkeypatch.setattr(deploy, 'run_container', lambda *args, **kwargs: starts.append(args))
    with pytest.raises(release.ReleaseError, match='Original container identity'):
        deploy.candidate_start()
    assert starts == []


@pytest.mark.parametrize('ready,expected', [
    ({'ready': True, 'capabilities': {'video_presence': 'ready'}}, True),
    ({'ready': False, 'capabilities': {'video_presence': 'ready'}}, False),
    ({'ready': True, 'capabilities': {'video_presence': 'unavailable'}}, False),
    ({'ready': True, 'capabilities': {}}, False),
])
def test_readiness_requires_boolean_and_capability(monkeypatch, ready, expected) -> None:
    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, url, timeout):
            payload = ready if url.endswith('/health/ready') else {
                'paths': {'/api/v2/verify/video/presence': {}},
            }
            return Response(json.dumps(payload).encode())

    ticks = iter((0, 0, 10))
    monkeypatch.setattr(release.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    monkeypatch.setattr(release.urllib.request, 'build_opener', lambda *_: Opener())
    if expected:
        release.wait_http('http://127.0.0.1:18012', presence=True, timeout=1)
    else:
        with pytest.raises(release.ReleaseError, match='Readiness timeout'):
            release.wait_http('http://127.0.0.1:18012', presence=True, timeout=1)


def test_config_preserves_every_existing_setting_and_enabled_modnet() -> None:
    original = 'resources: {max_video_bytes: 99}\nmodels:\n  portrait_matting: {enabled: true}\nlegacy: {token: secret}\n'
    merged = release.merged_config(original)
    assert merged.startswith(original)
    payload = yaml.safe_load(merged)
    assert payload.pop('video_presence') == yaml.safe_load(release.PRESENCE_BLOCK)['video_presence']
    assert payload == yaml.safe_load(original)
    assert release.merged_config(merged) == merged


def make_archive(path: Path, name: str, *, commit: str = COMMIT, symlink: bool = False) -> None:
    with tarfile.open(path, 'w', format=tarfile.PAX_FORMAT, pax_headers={'comment': commit}) as archive:
        member = tarfile.TarInfo(name)
        if symlink:
            member.type = tarfile.SYMTYPE
            member.linkname = '/etc/passwd'
            archive.addfile(member)
        else:
            member.size = 4
            archive.addfile(member, io.BytesIO(b'data'))


@pytest.mark.parametrize('name,symlink', [('../outside', False), ('/absolute', False),
                                         ('FaceFeatureExtractorAPI/link', True)])
def test_archive_rejects_escape_and_links_before_extracting(tmp_path: Path, name: str, symlink: bool) -> None:
    archive = tmp_path / 'release.tar'
    target = tmp_path / 'source'
    make_archive(archive, name, symlink=symlink)
    with pytest.raises(release.ReleaseError):
        release.extract_archive(archive, target, COMMIT)
    assert not target.exists()


def test_archive_requires_matching_git_commit_and_no_overwrite(tmp_path: Path) -> None:
    archive = tmp_path / 'release.tar'
    target = tmp_path / 'source'
    make_archive(archive, 'FaceFeatureExtractorAPI/file.txt')
    with pytest.raises(release.ReleaseError, match='provenance'):
        release.extract_archive(archive, target, 'b' * 40)
    release.extract_archive(archive, target, COMMIT)
    assert (target / 'FaceFeatureExtractorAPI/file.txt').read_text() == 'data'
    with pytest.raises(release.ReleaseError, match='already exists'):
        release.extract_archive(archive, target, COMMIT)


def test_protected_snapshot_ignores_health_history_but_detects_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    protected = item(release.PROTECTED_ID)
    monkeypatch.setattr(release, 'inspect', lambda _: protected)
    before = release.protected_snapshot()
    protected['State']['Health'] = {'Status': 'healthy', 'Log': ['changed']}
    assert release.protected_snapshot() == before
    protected['State']['StartedAt'] = 'restarted'
    assert release.protected_snapshot() != before
    protected['Id'] = 'different'
    with pytest.raises(release.ReleaseError):
        release.protected_snapshot()


def test_api_launch_is_restricted_and_secrets_never_enter_argv(deploy, monkeypatch) -> None:
    monkeypatch.setattr(release, 'inspect', lambda _: None)
    calls = []
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: calls.append((args, kwargs)))
    old_api = {'Config': {'Env': ['FACE_ALGO_LEGACY_TOKEN=private-token', 'UNSAFE_VARIABLE=discard']}}
    deploy.run_container(deploy.candidate, console=False, candidate=True, old_api=old_api)
    args, kwargs = calls[0]
    assert '127.0.0.1:18012:8000' in args
    assert '<server-37>:18002:8000' not in args
    assert 'private-token' not in ' '.join(args)
    assert kwargs['env']['FACE_ALGO_LEGACY_TOKEN'] == 'private-token'
    assert 'UNSAFE_VARIABLE' not in args
    assert all(option in args for option in ('--read-only', '--cap-drop', 'ALL', '--pids-limit', '512',
                                             '--memory', '6g', '--memory-swap', 'no-new-privileges'))
    assert all(key + '=1' in args for key in release.THREAD_ENV)
    assert any('size=2g,uid=10001,gid=10001,mode=0700' in arg for arg in args)
    assert '18001' not in ' '.join(args)


def test_console_launch_has_fixed_origin_upstream_and_bounds(deploy, monkeypatch) -> None:
    monkeypatch.setattr(release, 'inspect', lambda _: None)
    calls = []
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: calls.append(args))
    deploy.run_container(release.CONSOLE, console=True)
    args = calls[0]
    assert '<server-37>:18003:8080' in args
    assert 'VISUAL_CONSOLE_UPSTREAM=http://<server-37>:18002' in args
    assert 'VISUAL_CONSOLE_PUBLIC_ORIGIN=http://<server-37>:18003' in args
    assert all(value in args for value in ('0.5', '256m', '128', 'max-size=5m', 'max-file=3'))


def test_rollback_preflights_all_identities_before_any_mutation(deploy, monkeypatch) -> None:
    deploy.state['originals'] = {release.API: 'original-api', release.CONSOLE: 'original-console'}
    containers = {
        release.API: item('new-api', owned=True),
        release.API + '-prev-872c969': item('original-api'),
        release.CONSOLE: item('unrelated-console'),
        release.CONSOLE + '-prev-872c969': item('original-console'),
    }
    monkeypatch.setattr(release, 'inspect', containers.get)
    calls = []
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(release.ReleaseError, match='unrelated'):
        deploy.rollback()
    assert calls == []


def test_rollback_restores_partial_switch_and_leaves_older_backup(deploy, monkeypatch) -> None:
    deploy.state['originals'] = {release.API: 'original-api', release.CONSOLE: 'original-console'}
    containers = {
        release.API: item('new-api', owned=True),
        release.API + '-prev-872c969': item('original-api'),
        release.CONSOLE: item('original-console'),
        release.API + '-prev-6200689': item('ancient-api'),
    }
    calls = []
    monkeypatch.setattr(release, 'inspect', lambda name: copy.deepcopy(containers.get(name)))
    monkeypatch.setattr(release, 'wait_http', lambda *args, **kwargs: None)

    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[0] == 'rm':
            name = next(name for name, value in containers.items() if value['Id'] == args[2])
            del containers[name]
        elif args[0] == 'rename':
            name = next(name for name, value in containers.items() if value['Id'] == args[1])
            containers[args[2]] = containers.pop(name)
        return ''

    monkeypatch.setattr(release, 'docker', fake_docker)
    deploy.rollback()
    assert containers[release.API]['Id'] == 'original-api'
    assert containers[release.CONSOLE]['Id'] == 'original-console'
    assert containers[release.API + '-prev-6200689']['Id'] == 'ancient-api'
    assert deploy.state['stage'] == 'rolled-back'
    assert all(release.PROTECTED not in call for call in calls)


def test_promotion_failure_always_calls_rollback(deploy, monkeypatch) -> None:
    deploy.state = {'stage': 'candidate', 'config_sha256': 'config-hash'}
    containers = {deploy.candidate: item('candidate', owned=True),
                  release.API: item('original-api'), release.CONSOLE: item('original-console')}
    containers[release.API]['Config']['Image'] = 'face-algorithm:task-2026-001-872c969'
    containers[release.CONSOLE]['Config']['Image'] = 'face-algorithm-visual-console:task-2026-001-872c969'
    monkeypatch.setattr(release, 'inspect', containers.get)
    monkeypatch.setattr(release, 'sha256', lambda _: 'config-hash')
    monkeypatch.setattr(release, 'wait_http', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, 'verify_images', lambda: None)
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: (_ for _ in ()).throw(release.ReleaseError('failure')))
    rollbacks = []
    monkeypatch.setattr(deploy, 'rollback', lambda: rollbacks.append(True))
    with pytest.raises(release.ReleaseError, match='failure'):
        deploy.promote()
    assert rollbacks == [True]
    assert deploy.state['originals'] == {release.API: 'original-api', release.CONSOLE: 'original-console'}
    assert deploy.state_path.exists()
