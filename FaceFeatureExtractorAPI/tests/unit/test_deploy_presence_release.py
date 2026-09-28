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
BASELINE_COMMIT = '1e6b57b5b1dd32693497c21a0ce0039b4f77fc1b'
BASELINE_SHORT = BASELINE_COMMIT[:12]
PRESENCE_ADDITIONS = {
    'threshold': None, 'threshold_id': None, 'max_supplemental_frames': 2,
}
NEW_DTO_FIELDS = {
    'comparison_status', 'threshold', 'threshold_id', 'policy_fingerprint',
    'sampling_strategy', 'requested_sampling_interval_seconds', 'base_frames',
    'supplemental_frames', 'compared_candidates', 'failed_candidates', 'quality_warnings',
}


def presence_schema() -> dict:
    from face_algorithm.presence.contracts import VideoPresenceResult

    model = VideoPresenceResult.model_json_schema(ref_template='#/components/schemas/{model}')
    definitions = model.pop('$defs')
    return {
        'info': {'version': '2.2.0'},
        'paths': {'/api/v2/verify/video/presence': {'post': {'responses': {'200': {
            'content': {'application/json': {'schema': {'$ref': '#/components/schemas/VideoPresenceResult'}}},
        }}}}},
        'components': {'schemas': {'VideoPresenceResult': model, **definitions}},
    }


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


def test_release_baseline_is_the_deployed_presence_revision() -> None:
    assert release.OLD_REVISION == BASELINE_SHORT
    assert release.OLD_SOURCE == release.ROOT / ('source-' + BASELINE_COMMIT) / 'FaceFeatureExtractorAPI'
    assert release.OLD_CONFIG == release.ROOT / 'runtime' / ('algorithm-' + BASELINE_COMMIT + '.yaml')


def test_presence_defaults_match_the_new_source_policy() -> None:
    source = yaml.safe_load((Path(__file__).parents[2] / 'config/algorithm.yaml').read_text(encoding='utf-8'))
    assert yaml.safe_load(release.PRESENCE_BLOCK)['video_presence'] == source['video_presence']


@pytest.mark.parametrize('custom_interval', [False, True])
def test_build_uses_current_baseline_validator_and_never_overwrites_custom_policy(
    deploy, monkeypatch, tmp_path, custom_interval,
) -> None:
    source_config = yaml.safe_load((Path(__file__).parents[2] / 'config/algorithm.yaml').read_text(encoding='utf-8'))
    new_policy = source_config['video_presence']
    old_policy = {key: value for key, value in new_policy.items() if key not in PRESENCE_ADDITIONS}
    if custom_interval:
        old_policy['sample_interval_seconds'] = 0.75
    old_config = {'models': {'portrait_matting': {'enabled': True}}, 'video_presence': old_policy,
                  'resources': {'max_video_bytes': 99}}
    old_source = tmp_path / 'old-source'
    old_source.mkdir()
    (old_source / 'requirements-cpu.txt').write_bytes(b'unchanged requirements\n')
    (old_source / 'Dockerfile.offline').write_text('FROM existing-image\n')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    old_path = runtime / 'old.yaml'
    old_path.write_text(yaml.safe_dump(old_config), encoding='utf-8')
    checkpoint = runtime / 'checkpoint'
    checkpoint.write_bytes(b'checkpoint')
    monkeypatch.setattr(release, 'OLD_SOURCE', old_source)
    monkeypatch.setattr(release, 'OLD_CONFIG', old_path)
    monkeypatch.setattr(release, 'CHECKPOINT', checkpoint)
    files = {
        'requirements-cpu.txt': 'unchanged requirements\n',
        'models.manifest.json': '{"models": []}',
        'config/algorithm.yaml': yaml.safe_dump({'video_presence': new_policy}),
    }
    archive = tmp_path / ('release-' + COMMIT + '.tar')
    with tarfile.open(archive, 'w', format=tarfile.PAX_FORMAT, pax_headers={'comment': COMMIT}) as bundle:
        for name, content in files.items():
            data = content.encode()
            member = tarfile.TarInfo('FaceFeatureExtractorAPI/' + name)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(release, 'inspect', lambda *args, **kwargs: None)
    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[0] == 'run':
            code = args[-1]
            for mount, local in (('/old.yaml', old_path), ('/new.yaml', deploy.config),
                                 ('/source.yaml', deploy.source / 'config/algorithm.yaml')):
                code = code.replace(repr(mount), repr(str(local)))
            try:
                exec(code, {})
            except AssertionError as error:
                raise release.ReleaseError('Configuration validation failed') from error
        return ''

    monkeypatch.setattr(release, 'docker', fake_docker)
    if custom_interval:
        with pytest.raises(release.ReleaseError, match='Configuration validation'):
            deploy.build()
        assert not any(call[0] == 'build' for call in calls)
    else:
        deploy.build()
        assert deploy.state['stage'] == 'built'
        assert calls[0][-4] == 'face-algorithm:task-2026-001-' + BASELINE_SHORT
    merged = yaml.safe_load(deploy.config.read_text(encoding='utf-8'))
    assert merged['models']['portrait_matting']['enabled'] is True
    assert merged['resources'] == old_config['resources']
    assert all(merged['video_presence'][key] == value for key, value in old_policy.items())


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
            payload = ready if url.endswith('/health/ready') else presence_schema()
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


@pytest.mark.parametrize('defect', ['old-version', 'wrong-response', 'old-calibration',
                                   'old-evidence', *sorted(NEW_DTO_FIELDS)])
def test_readiness_rejects_old_or_incomplete_presence_contract(monkeypatch, defect) -> None:
    schema = presence_schema()
    properties = schema['components']['schemas']['VideoPresenceResult']['properties']
    if defect == 'old-version':
        schema['info']['version'] = '2.1.0'
    elif defect == 'wrong-response':
        schema['paths']['/api/v2/verify/video/presence']['post']['responses']['200']['content']['application/json']['schema']['$ref'] = '#/components/schemas/OldResult'
    elif defect == 'old-calibration':
        properties['calibration_status']['enum'] = ['UNCALIBRATED']
    elif defect == 'old-evidence':
        del schema['components']['schemas']['PresenceEvidenceFrame']['properties']['candidate_index']
    else:
        del properties[defect]

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, url, timeout):
            payload = {'ready': True, 'capabilities': {'video_presence': 'ready'}} if url.endswith('/health/ready') else schema
            return Response(json.dumps(payload).encode())

    ticks = iter((0, 0, 10))
    monkeypatch.setattr(release.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    monkeypatch.setattr(release.urllib.request, 'build_opener', lambda *_: Opener())
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


def test_existing_presence_gets_only_missing_additions_and_is_idempotent() -> None:
    original = ('models:\n  portrait_matting: {enabled: true}\nvideo_presence:\n'
                '  mode: uncalibrated\n  sample_interval_seconds: 0.75\n'
                '  max_analyzed_frames: 300\n  max_supplemental_frames: 1\n'
                'route_deadlines: {video_full_seconds: 120}\n')
    expected = yaml.safe_load(original)
    expected['video_presence'].update(threshold=None, threshold_id=None)
    merged = release.merged_config(original)
    assert yaml.safe_load(merged) == expected
    assert release.merged_config(merged) == merged
    assert '  max_supplemental_frames: 1\n' in merged
    assert 'route_deadlines: {video_full_seconds: 120}\n' in merged


def test_existing_trial_threshold_is_not_overwritten() -> None:
    original = 'video_presence:\n  mode: configured\n  threshold: 0.61\n  threshold_id: explicit-trial\n'
    expected = yaml.safe_load(original)
    expected['video_presence']['max_supplemental_frames'] = 2
    assert yaml.safe_load(release.merged_config(original)) == expected


@pytest.mark.parametrize('original', [
    'video_presence: {mode: uncalibrated}\n',
    '"video_presence":\n  mode: uncalibrated\n',
    'video_presence:\n  mode: uncalibrated\nvideo_presence:\n  mode: configured\n',
    'video_presence:\n  mode: uncalibrated\n  threshold: null\n  threshold: 0.5\n',
    'video_presence:\n  <<: *defaults\n',
    'video_presence:\n    mode: uncalibrated\n',
])
def test_config_patch_rejects_ambiguous_presence_without_guessing(original) -> None:
    with pytest.raises(release.ReleaseError, match='presence'):
        release.merged_config(original)


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
        release.API + '-prev-' + release.OLD_REVISION: item('original-api'),
        release.CONSOLE: item('unrelated-console'),
        release.CONSOLE + '-prev-' + release.OLD_REVISION: item('original-console'),
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
        release.API + '-prev-' + release.OLD_REVISION: item('original-api'),
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
    containers[release.API]['Config']['Image'] = 'face-algorithm:task-2026-001-' + BASELINE_SHORT
    containers[release.CONSOLE]['Config']['Image'] = 'face-algorithm-visual-console:task-2026-001-' + BASELINE_SHORT
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


def test_promotion_preserves_older_backups_and_uses_current_baseline(deploy, monkeypatch) -> None:
    deploy.state = {'stage': 'candidate', 'config_sha256': 'config-hash'}
    containers = {deploy.candidate: item('candidate', owned=True),
                  release.API: item('original-api'), release.CONSOLE: item('original-console'),
                  release.API + '-prev-872c969': item('ancient-api'),
                  release.CONSOLE + '-prev-872c969': item('ancient-console')}
    containers[release.API]['Config']['Image'] = 'face-algorithm:task-2026-001-' + BASELINE_SHORT
    containers[release.CONSOLE]['Config']['Image'] = 'face-algorithm-visual-console:task-2026-001-' + BASELINE_SHORT
    monkeypatch.setattr(release, 'inspect', lambda name: copy.deepcopy(containers.get(name)))
    monkeypatch.setattr(release, 'sha256', lambda _: 'config-hash')
    monkeypatch.setattr(release, 'wait_http', lambda *args, **kwargs: None)
    monkeypatch.setattr(release, 'wait_port_free', lambda *args: None)
    monkeypatch.setattr(deploy, 'verify_images', lambda: None)
    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[0] == 'rm':
            name = next(name for name, value in containers.items() if value['Id'] == args[2])
            del containers[name]
        elif args[0] == 'rename':
            name = next(name for name, value in containers.items() if value['Id'] == args[1])
            containers[args[2]] = containers.pop(name)
        return ''

    def start(name, **kwargs):
        containers[name] = item('new-' + name, owned=True)

    monkeypatch.setattr(release, 'docker', fake_docker)
    monkeypatch.setattr(deploy, 'run_container', start)
    deploy.promote()
    assert deploy.state['stage'] == 'promoted'
    assert containers[release.API + '-prev-' + BASELINE_SHORT]['Id'] == 'original-api'
    assert containers[release.CONSOLE + '-prev-' + BASELINE_SHORT]['Id'] == 'original-console'
    assert containers[release.API + '-prev-872c969']['Id'] == 'ancient-api'
    assert containers[release.CONSOLE + '-prev-872c969']['Id'] == 'ancient-console'
    assert all('ancient-api' not in call and 'ancient-console' not in call for call in calls)


@pytest.mark.parametrize('target', [release.API, release.CONSOLE])
def test_promotion_rejects_wrong_baseline_before_any_mutation(deploy, monkeypatch, target) -> None:
    deploy.state = {'stage': 'candidate', 'config_sha256': 'config-hash'}
    containers = {deploy.candidate: item('candidate', owned=True),
                  release.API: item('original-api'), release.CONSOLE: item('original-console')}
    containers[release.API]['Config']['Image'] = 'face-algorithm:task-2026-001-' + BASELINE_SHORT
    containers[release.CONSOLE]['Config']['Image'] = 'face-algorithm-visual-console:task-2026-001-' + BASELINE_SHORT
    containers[target]['Config']['Image'] = 'unexpected-image'
    monkeypatch.setattr(release, 'inspect', containers.get)
    monkeypatch.setattr(release, 'sha256', lambda _: 'config-hash')
    monkeypatch.setattr(release, 'wait_http', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, 'verify_images', lambda: None)
    calls = []
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(release.ReleaseError, match='approved baseline'):
        deploy.promote()
    assert calls == []
    assert deploy.state['stage'] == 'candidate'
    assert 'originals' not in deploy.state
