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
BASELINE_COMMIT = 'd78e723b8fb8737b09e12859e57aa9856a640db6'
BASELINE_SHORT = BASELINE_COMMIT[:12]
BASELINE_IDS = {
    release.API: 'cf671e39ce84b74ecbd4acd0679e76e5090070ea67daba006fb647d77edb0c76',
    release.CONSOLE: '65eaacd8f838d0f02c2a271ed0969321ceb8601391d8ccd24e4b11de240872a5',
}
BASELINE_CONFIG_SHA256 = 'c0d27189eb153cedb0b4d81890b18e2b99a88f5cedec0ba2a63785a882be0287'
PORTRAIT_FIELDS = {
    'success', 'suggestion', 'reason_codes', 'model_versions', 'config_version',
    'processing_time_ms', 'trace_id', 'template_version', 'output_width', 'output_height',
    'output_format', 'jpeg_quality', 'original_width', 'original_height',
    'rotation_quarter_turns', 'placement', 'matting_quality', 'image_base64',
}
NEW_DTO_FIELDS = {
    'comparison_status', 'threshold', 'threshold_id', 'policy_fingerprint',
    'sampling_strategy', 'requested_sampling_interval_seconds', 'base_frames',
    'supplemental_frames', 'compared_candidates', 'failed_candidates', 'quality_warnings',
}


def presence_schema() -> dict:
    from face_algorithm.presence.contracts import VideoPresenceResult
    from api.openapi import PortraitResponse

    model = VideoPresenceResult.model_json_schema(ref_template='#/components/schemas/{model}')
    definitions = model.pop('$defs')
    portrait = PortraitResponse.model_json_schema(ref_template='#/components/schemas/{model}')
    definitions.update(portrait.pop('$defs'))
    definitions['ReasonCode']['enum'] = sorted(set(definitions['ReasonCode']['enum']) | {
        'PORTRAIT_SOURCE_LIMITED', 'PORTRAIT_UPSCALE_LIMITED',
    })
    return {
        'info': {'version': '2.3.0'},
        'paths': {'/api/v2/verify/video/presence': {'post': {'responses': {'200': {
            'content': {'application/json': {'schema': {'$ref': '#/components/schemas/VideoPresenceResult'}}},
        }}}}, '/api/v2/portrait/generate': {'post': {
            'requestBody': {'required': True, 'content': {'multipart/form-data': {
                'schema': {'$ref': '#/components/schemas/PortraitUpload'},
            }}},
            'responses': {'200': {'content': {'application/json': {
                'schema': {'$ref': '#/components/schemas/PortraitResponse'},
            }}}},
        }}},
        'components': {'schemas': {'VideoPresenceResult': model, 'PortraitResponse': portrait,
                                  'PortraitUpload': {'required': ['image'], 'properties': {
                                      'image': {'type': 'string', 'format': 'binary'},
                                  }}, **definitions}},
    }


def item(identifier: str, *, owned: bool = False) -> dict:
    prefix = 'face-algorithm-visual-console' if 'console' in identifier else 'face-algorithm'
    return {'Id': identifier, 'Image': 'sha256:image',
            'State': {'Running': True, 'StartedAt': 'start', 'Health': {'Status': 'healthy'}},
            'Mounts': [{'Destination': '/app/config/algorithm.yaml', 'Source': str(release.OLD_CONFIG),
                        'RW': False}],
            'HostConfig': {}, 'Config': {'Image': prefix + ':task-2026-001-' + BASELINE_SHORT,
                                       'Env': [], 'Labels': {
                release.REVISION_LABEL: COMMIT, release.OWNER_LABEL: release.API,
            } if owned else {release.REVISION_LABEL: BASELINE_COMMIT, release.OWNER_LABEL: release.API}}}


@pytest.fixture
def deploy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> release.Release:
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    old_config = tmp_path / 'baseline.yaml'
    old_config.write_text(approved_config(), encoding='utf-8')
    monkeypatch.setattr(release, 'OLD_CONFIG', old_config)
    monkeypatch.setattr(release, 'OLD_CONFIG_SHA256', release.sha256(old_config), raising=False)
    monkeypatch.setattr(release, 'OLD_CONTAINER_IDS',
                        {release.API: 'original-api', release.CONSOLE: 'original-console'}, raising=False)
    return release.Release(COMMIT)


def approved_config() -> str:
    return ('models:\n  portrait_matting: {enabled: true}\n'
            'video_presence:\n  mode: configured\n  threshold: 0.5\n'
            '  threshold_id: TASK-2026-001-presence-trial-050-20260928\n'
            '  sample_interval_seconds: 0.75\n  max_supplemental_frames: 1\n'
            'portrait_template:\n  version: head-shoulders-480x640-v1 # preserve comment\n'
            '  output_width: 480\n  output_height: 640\n'
            '  background_rgb: [255, 255, 255]\n'
            'resources: {max_video_bytes: 99}\n')


def test_commit_must_be_full_unambiguous_lowercase_sha() -> None:
    assert release.full_commit(COMMIT) == COMMIT
    for invalid in ('a' * 12, '../escape', 'A' * 40, 'a' * 39 + 'x'):
        with pytest.raises(argparse.ArgumentTypeError):
            release.full_commit(invalid)


def test_release_baseline_is_the_deployed_presence_revision() -> None:
    assert release.OLD_COMMIT == BASELINE_COMMIT
    assert release.OLD_REVISION == BASELINE_SHORT
    assert release.OLD_SOURCE == release.ROOT / ('source-' + BASELINE_COMMIT) / 'FaceFeatureExtractorAPI'
    assert release.OLD_CONFIG == release.ROOT / 'runtime' / ('algorithm-' + BASELINE_COMMIT + '.yaml')
    assert release.OLD_CONTAINER_IDS == BASELINE_IDS
    assert release.OLD_CONFIG_SHA256 == BASELINE_CONFIG_SHA256


def test_presence_defaults_match_the_new_source_policy() -> None:
    source = yaml.safe_load((Path(__file__).parents[2] / 'config/algorithm.yaml').read_text(encoding='utf-8'))
    assert yaml.safe_load(release.PRESENCE_BLOCK)['video_presence'] == source['video_presence']


@pytest.mark.parametrize('custom_interval', [False, True])
def test_build_uses_current_baseline_validator_and_never_overwrites_custom_policy(
    deploy, monkeypatch, tmp_path, custom_interval,
) -> None:
    source_config = yaml.safe_load((Path(__file__).parents[2] / 'config/algorithm.yaml').read_text(encoding='utf-8'))
    new_policy = source_config['video_presence']
    old_policy = dict(new_policy, mode='configured', threshold=0.5,
                      threshold_id='TASK-2026-001-presence-trial-050-20260928')
    if custom_interval:
        old_policy['sample_interval_seconds'] = 0.75
    old_config = {'models': {'portrait_matting': {'enabled': True}}, 'video_presence': old_policy,
                  'resources': {'max_video_bytes': 99},
                  'portrait_template': {'version': 'head-shoulders-480x640-v1', 'output_width': 480}}
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
    monkeypatch.setattr(release, 'OLD_CONFIG_SHA256', release.sha256(old_path), raising=False)
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
    containers = {release.API: item('original-api'), release.CONSOLE: item('original-console')}
    monkeypatch.setattr(release, 'inspect', lambda name, **kwargs: containers.get(name))
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
    deploy.build()
    assert deploy.state['stage'] == 'built'
    assert calls[0][-4] == 'face-algorithm:task-2026-001-' + BASELINE_SHORT
    merged = yaml.safe_load(deploy.config.read_text(encoding='utf-8'))
    assert merged['models']['portrait_matting']['enabled'] is True
    assert merged['resources'] == old_config['resources']
    assert merged['video_presence'] == old_policy
    expected = copy.deepcopy(old_config)
    expected['portrait_template'].update(mode='face_archive', version='face-archive-480x640-v1')
    assert merged == expected


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('mode', ['', '  mode: head_shoulders # strict compatibility\n'])
def test_portrait_migration_changes_only_mode_and_version(newline, mode) -> None:
    original = approved_config().replace('portrait_template:\n', 'portrait_template:\n' + mode)
    original = original.replace('\n', newline)
    migrated = release.portrait_config(original)
    expected = yaml.safe_load(original)
    expected['portrait_template'].update(mode='face_archive', version='face-archive-480x640-v1')
    assert yaml.safe_load(migrated) == expected
    assert migrated.split('portrait_template:')[0] == original.split('portrait_template:')[0]
    assert 'face-archive-480x640-v1 # preserve comment' in migrated
    assert 'resources: {max_video_bytes: 99}' + newline in migrated
    assert release.portrait_config(migrated) == migrated


@pytest.mark.parametrize('section', [
    '', 'portrait_template: {}\n', '"portrait_template":\n  version: old\n',
    'portrait_template:\n  version: old\nportrait_template:\n  version: old\n',
    'portrait_template:\n  version: head-shoulders-480x640-v1\n  version: duplicate\n',
    'portrait_template:\n  mode: head_shoulders\n  mode: head_shoulders\n',
    'portrait_template:\n  <<: *defaults\n', 'portrait_template:\n    version: old\n',
    'portrait_template:\n  version: unexpected\n',
    'portrait_template:\n  version: head-shoulders-480x640-v1\n  mode: unknown\n',
    'portrait_template: &shared\n  version: head-shoulders-480x640-v1\n',
    'portrait_template:\n  version: head-shoulders-480x640-v1\n  output_width: [480]\n',
    'portrait_template:\n  version: head-shoulders-480x640-v1\n  output_width: *alias\n',
])
def test_portrait_migration_refuses_missing_ambiguous_or_nonstandard_yaml(section) -> None:
    with pytest.raises(release.ReleaseError, match='portrait'):
        release.portrait_config('video_presence: {threshold: 0.5}\n' + section)


def run_config_validation(tmp_path, original, migrated):
    old = tmp_path / 'old.yaml'
    new = tmp_path / 'new.yaml'
    old.write_text(original, encoding='utf-8')
    new.write_text(migrated, encoding='utf-8')
    code = release.config_validation_script()
    code = code.replace("'/old.yaml'", repr(str(old))).replace("'/new.yaml'", repr(str(new)))
    exec(code, {})


def test_container_semantic_validation_preserves_all_data_and_custom_presence(tmp_path) -> None:
    original = approved_config()
    run_config_validation(tmp_path, original, release.portrait_config(original))


@pytest.mark.parametrize('defect', ['presence', 'modnet', 'resource', 'width', 'version', 'mode',
                                  'duplicate-original', 'duplicate-migrated', 'presence-baseline'])
def test_container_semantic_validation_rejects_unapproved_changes(tmp_path, defect) -> None:
    original = approved_config()
    migrated = release.portrait_config(original)
    if defect == 'presence':
        migrated = migrated.replace('threshold: 0.5', 'threshold: null')
    elif defect == 'modnet':
        migrated = migrated.replace('enabled: true', 'enabled: false')
    elif defect == 'resource':
        migrated = migrated.replace('max_video_bytes: 99', 'max_video_bytes: 100')
    elif defect == 'width':
        migrated = migrated.replace('output_width: 480', 'output_width: 481')
    elif defect == 'version':
        migrated = migrated.replace('face-archive-480x640-v1', 'head-shoulders-480x640-v1')
    elif defect == 'mode':
        migrated = migrated.replace('mode: face_archive', 'mode: head_shoulders')
    elif defect == 'duplicate-original':
        original += 'resources: {max_video_bytes: 99}\n'
    elif defect == 'duplicate-migrated':
        migrated += 'resources: {max_video_bytes: 99}\n'
    elif defect == 'presence-baseline':
        original = original.replace('threshold: 0.5', 'threshold: 0.6')
        migrated = migrated.replace('threshold: 0.5', 'threshold: 0.6')
    with pytest.raises((AssertionError, ValueError, yaml.YAMLError)):
        run_config_validation(tmp_path, original, migrated)


@pytest.mark.parametrize('target', [release.API, release.CONSOLE])
@pytest.mark.parametrize('defect', ['id', 'image', 'revision', 'owner', 'health', 'stopped', 'config-sha', 'mount'])
def test_baseline_guard_rejects_changed_runtime(deploy, monkeypatch, target, defect) -> None:
    containers = {release.API: item('original-api'), release.CONSOLE: item('original-console')}
    monkeypatch.setattr(release, 'inspect', containers.get)
    if defect == 'id':
        containers[target]['Id'] = 'replacement'
    elif defect == 'image':
        containers[target]['Config']['Image'] = 'unapproved-image'
    elif defect == 'revision':
        containers[target]['Config']['Labels'][release.REVISION_LABEL] = 'b' * 40
    elif defect == 'owner':
        containers[target]['Config']['Labels'][release.OWNER_LABEL] = 'other-owner'
    elif defect == 'health':
        containers[target]['State']['Health']['Status'] = 'unhealthy'
    elif defect == 'stopped':
        containers[target]['State']['Running'] = False
    elif defect == 'config-sha':
        release.OLD_CONFIG.write_text('changed', encoding='utf-8')
    else:
        containers[release.API]['Mounts'][0]['Source'] = '/unexpected.yaml'
    calls = []
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(release.ReleaseError, match='baseline'):
        release.baseline_snapshot()
    assert calls == []


def test_baseline_guard_accepts_exact_current_runtime(deploy, monkeypatch) -> None:
    containers = {release.API: item('original-api'), release.CONSOLE: item('original-console')}
    monkeypatch.setattr(release, 'inspect', containers.get)
    assert release.baseline_snapshot() == containers


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
    monkeypatch.setattr(release, 'sha256', lambda path: 'config-hash' if path == deploy.config else release.OLD_CONFIG_SHA256)
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
    monkeypatch.setattr(release, 'sha256', lambda path: 'config-hash' if path == deploy.config else release.OLD_CONFIG_SHA256)
    monkeypatch.setattr(deploy, 'verify_images', lambda: None)
    starts = []
    monkeypatch.setattr(deploy, 'run_container', lambda *args, **kwargs: starts.append(args))
    with pytest.raises(release.ReleaseError, match='Original container identity'):
        deploy.candidate_start()
    assert starts == []


@pytest.mark.parametrize('ready,expected', [
    ({'ready': True, 'capabilities': {'video_presence': 'ready', 'portrait': 'ready'}}, True),
    ({'ready': True, 'capabilities': {'video_presence': 'ready', 'portrait': 'unavailable'}}, False),
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
            payload = {'ready': True, 'capabilities': {'video_presence': 'ready', 'portrait': 'ready'}} if url.endswith('/health/ready') else schema
            return Response(json.dumps(payload).encode())

    ticks = iter((0, 0, 10))
    monkeypatch.setattr(release.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    monkeypatch.setattr(release.urllib.request, 'build_opener', lambda *_: Opener())
    with pytest.raises(release.ReleaseError, match='Readiness timeout'):
        release.wait_http('http://127.0.0.1:18012', presence=True, timeout=1)


@pytest.mark.parametrize('defect', ['version', 'route', 'request-field', 'extra-request',
                                  'request-required', 'request-binary', 'response', 'extra-response',
                                  'missing-response', 'shoulder-field', 'placement-field', 'reason-field',
                                  'source-warning', 'upscale-warning'])
def test_readiness_rejects_changed_portrait_contract(monkeypatch, defect) -> None:
    schema = presence_schema()
    route = schema['paths']['/api/v2/portrait/generate']['post']
    models = schema['components']['schemas']
    if defect == 'version':
        schema['info']['version'] = '2.2.0'
    elif defect == 'route':
        del schema['paths']['/api/v2/portrait/generate']
    elif defect == 'request-field':
        models['PortraitUpload']['properties']['photo'] = models['PortraitUpload']['properties'].pop('image')
    elif defect == 'extra-request':
        models['PortraitUpload']['properties']['mode'] = {'type': 'string'}
    elif defect == 'request-required':
        models['PortraitUpload']['required'] = []
    elif defect == 'request-binary':
        models['PortraitUpload']['properties']['image']['format'] = 'base64'
    elif defect == 'response':
        route['responses']['200']['content']['application/json']['schema']['$ref'] = '#/components/schemas/Other'
    elif defect == 'extra-response':
        models['PortraitResponse']['properties']['quality_warnings'] = {'type': 'array'}
    elif defect == 'missing-response':
        del models['PortraitResponse']['properties']['matting_quality']
    elif defect == 'shoulder-field':
        del models['PortraitMattingQuality']['properties']['shoulder_depth_ratio']
    elif defect == 'placement-field':
        del models['PortraitPlacement']['properties']['affine']
    elif defect == 'reason-field':
        models['PortraitResponse']['properties']['reason_codes']['items'] = {'type': 'string'}
    else:
        code = 'PORTRAIT_SOURCE_LIMITED' if defect == 'source-warning' else 'PORTRAIT_UPSCALE_LIMITED'
        models['ReasonCode']['enum'].remove(code)

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, url, timeout):
            payload = {'ready': True, 'capabilities': {'video_presence': 'ready', 'portrait': 'ready'}} if url.endswith('/health/ready') else schema
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
    monkeypatch.setattr(release, 'wait_restored_api', lambda *args, **kwargs: None, raising=False)

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
    monkeypatch.setattr(release, 'sha256', lambda path: 'config-hash' if path == deploy.config else release.OLD_CONFIG_SHA256)
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
    monkeypatch.setattr(release, 'sha256', lambda path: 'config-hash' if path == deploy.config else release.OLD_CONFIG_SHA256)
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
    monkeypatch.setattr(release, 'sha256', lambda path: 'config-hash' if path == deploy.config else release.OLD_CONFIG_SHA256)
    monkeypatch.setattr(release, 'wait_http', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, 'verify_images', lambda: None)
    calls = []
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(release.ReleaseError, match='approved baseline'):
        deploy.promote()
    assert calls == []
    assert deploy.state['stage'] == 'candidate'
    assert 'originals' not in deploy.state


@pytest.mark.parametrize('action', ['build', 'candidate_start', 'promote'])
def test_each_release_stage_rejects_changed_baseline_before_mutation(deploy, monkeypatch, action) -> None:
    deploy.state = {'stage': 'candidate' if action == 'promote' else 'built', 'config_sha256': 'config-hash'}
    containers = {deploy.candidate: item('candidate', owned=True),
                  release.API: item('replacement'), release.CONSOLE: item('original-console')}
    monkeypatch.setattr(release, 'inspect', containers.get)
    monkeypatch.setattr(release, 'sha256', lambda path: 'config-hash' if path == deploy.config else release.OLD_CONFIG_SHA256)
    monkeypatch.setattr(release, 'wait_http', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, 'verify_images', lambda: None)
    calls = []
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(release.ReleaseError, match='approved baseline'):
        getattr(deploy, action)()
    assert calls == []
    assert not deploy.state_path.exists()


@pytest.mark.parametrize('changed_config', [False, True])
def test_promoted_retry_uses_new_ownership_and_checks_config(deploy, monkeypatch, changed_config) -> None:
    deploy.state = {'stage': 'promoted', 'config_sha256': 'config-hash'}
    containers = {release.API: item('new-api', owned=True), release.CONSOLE: item('new-console', owned=True)}
    monkeypatch.setattr(release, 'inspect', containers.get)
    monkeypatch.setattr(release, 'sha256', lambda _: 'changed' if changed_config else 'config-hash')
    monkeypatch.setattr(deploy, 'verify_images', lambda: None)
    checks = []
    monkeypatch.setattr(release, 'wait_http', lambda *args, **kwargs: checks.append(args[0]))
    if changed_config:
        with pytest.raises(release.ReleaseError, match='Release config changed'):
            deploy.promote()
        assert checks == []
    else:
        deploy.promote()
        assert checks == ['http://<server-37>:18002', 'http://<server-37>:18003']


@pytest.mark.parametrize('defect', ['recorded-id', 'config', 'backup-label', 'backup-mount'])
def test_rollback_rejects_unapproved_restore_before_mutation(deploy, monkeypatch, defect) -> None:
    deploy.state['originals'] = {release.API: 'original-api', release.CONSOLE: 'original-console'}
    api_backup = release.API + '-prev-' + BASELINE_SHORT
    containers = {
        release.API: item('new-api', owned=True),
        api_backup: item('original-api'),
        release.CONSOLE: item('new-console', owned=True),
        release.CONSOLE + '-prev-' + BASELINE_SHORT: item('original-console'),
    }
    if defect == 'recorded-id':
        deploy.state['originals'][release.API] = 'unapproved-id'
    elif defect == 'config':
        release.OLD_CONFIG.write_text('changed', encoding='utf-8')
    elif defect == 'backup-label':
        containers[api_backup]['Config']['Labels'][release.REVISION_LABEL] = 'b' * 40
    else:
        containers[api_backup]['Mounts'][0]['RW'] = True
    monkeypatch.setattr(release, 'inspect', containers.get)
    calls = []
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(release.ReleaseError, match='baseline'):
        deploy.rollback()
    assert calls == []


def test_portrait_readiness_fixture_matches_current_dto_without_extra_fields() -> None:
    assert set(presence_schema()['components']['schemas']['PortraitResponse']['properties']) == PORTRAIT_FIELDS


def test_portrait_migration_accepts_full_runtime_template() -> None:
    original = (Path(__file__).parents[2] / 'config/algorithm.yaml').read_text(encoding='utf-8')
    original = original.replace('  mode: face_archive\n', '')
    original = original.replace('face-archive-480x640-v1', 'head-shoulders-480x640-v1')
    migrated = release.portrait_config(original)
    expected = yaml.safe_load(original)
    expected['portrait_template'].update(mode='face_archive', version='face-archive-480x640-v1')
    assert yaml.safe_load(migrated) == expected


def test_readiness_accepts_actual_public_openapi(monkeypatch) -> None:
    from api.app import create_app
    from api.services import DefaultAlgorithmServices
    from face_algorithm.runtime.config import load_config

    config = load_config(Path(__file__).parents[2] / 'config/algorithm.yaml')
    schema = create_app(config=config, services=DefaultAlgorithmServices(
        config=config, registry=object())).openapi()

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, url, timeout):
            payload = {'ready': True, 'capabilities': {'video_presence': 'ready', 'portrait': 'ready'}} if url.endswith('/health/ready') else schema
            return Response(json.dumps(payload).encode())

    monkeypatch.setattr(release.urllib.request, 'build_opener', lambda *_: Opener())
    release.wait_http('http://127.0.0.1:18012', presence=True, timeout=1)


@pytest.mark.parametrize('defect', [None, 'ready-503', 'ready-false', 'wrong-version',
                                  'portrait-unavailable', 'presence-unavailable', 'console-503'])
def test_rollback_records_success_only_after_old_api_ready_version_and_console(
    deploy, monkeypatch, capsys, defect,
) -> None:
    deploy.state['originals'] = {release.API: 'original-api', release.CONSOLE: 'original-console'}
    deploy.save('promoting')
    containers = {release.API: item('original-api'), release.CONSOLE: item('original-console')}
    monkeypatch.setattr(release, 'inspect', containers.get)
    actions = []
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: actions.append(args))
    ready = {'ready': defect != 'ready-false',
             'capabilities': {'portrait': 'ready', 'video_presence': 'ready'}}
    if defect in ('portrait-unavailable', 'presence-unavailable'):
        ready['capabilities']['portrait' if defect == 'portrait-unavailable' else 'video_presence'] = 'unavailable'
    schema = {'info': {'version': '2.3.0' if defect == 'wrong-version' else '2.2.0'}}
    urls = []

    class Response(io.BytesIO):
        def __init__(self, payload, status=200):
            super().__init__(json.dumps(payload).encode())
            self.status = status

    class Opener:
        def open(self, url, timeout):
            urls.append(url)
            # Persisted state must not claim restoration until every probe succeeds.
            assert json.loads(deploy.state_path.read_text())['stage'] == 'promoting'
            if url.endswith('/health/ready'):
                return Response(ready, 503 if defect == 'ready-503' else 200)
            if url.endswith('/openapi.json'):
                return Response(schema)
            # An old API /health/live 200 alone must never suffice.
            return Response({'status': 'live'},
                            503 if ':18003/' in url and defect == 'console-503' else 200)

    ticks = iter(range(1000))
    monkeypatch.setattr(release.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    monkeypatch.setattr(release.urllib.request, 'build_opener', lambda *_: Opener())
    if defect is None:
        deploy.rollback()
        assert urls == ['http://<server-37>:18002/health/ready',
                        'http://<server-37>:18002/openapi.json',
                        'http://<server-37>:18003/health/live']
        assert deploy.state['stage'] == 'rolled-back'
        assert json.loads(deploy.state_path.read_text())['stage'] == 'rolled-back'
        assert 'Original API and console restored' in capsys.readouterr().out
    else:
        with pytest.raises(release.ReleaseError, match='timeout'):
            deploy.rollback()
        assert deploy.state['stage'] == 'promoting'
        assert json.loads(deploy.state_path.read_text())['stage'] == 'promoting'
        assert 'Original API and console restored' not in capsys.readouterr().out
    assert actions == [('start', 'original-api'), ('start', 'original-console')]
