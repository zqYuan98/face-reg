"""Pinned, host-local release helper; never operates on the legacy port 18001.

Run on Linux37 after transferring ``git archive COMMIT`` to ROOT/release-COMMIT.tar.
Sequence: --mode build, --mode candidate, external acceptance, --mode promote.
No registry pushes, SSH, secrets, or configuration dumps occur. Pip uses the
existing offline wheelhouse; Docker's base/OS layers must already be cached.
Requires Docker BuildKit/build-context support and Python 3.9+ on the host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path('/home/<user>/deployments/face-algorithm-task-2026-001')
API = 'face-algorithm-task-2026-001'
CONSOLE = 'face-algorithm-visual-console-task-2026-001'
PROTECTED = 'face-algorithm'
PROTECTED_ID = 'd6773816222a2200ee76b0b23f35af51786cc64a46147e04933ff7d3281ece7f'
OLD_COMMIT = '4e6a3372ddeb0a53d4f3e024e0e072f636ae9f1a'
OLD_REVISION = OLD_COMMIT[:12]
OLD_SOURCE = ROOT / ('source-' + OLD_COMMIT) / 'FaceFeatureExtractorAPI'
OLD_CONFIG = ROOT / 'runtime' / ('algorithm-' + OLD_COMMIT + '.yaml')
OLD_CONFIG_SHA256 = '6d300c20cac3158ce1c5a720eb92f6a9231e3915e2a81e90bfe2653226cf7b5d'
OLD_CONTAINER_IDS = {
    API: '1cf05a422d89290099d03b2bead2cd9396e0451e7ca26beef68fba1bf23f150e',
    CONSOLE: 'e4af1efbeae7b5d189e00838f48b2a9b39aa320882f075e508d7b6121eef8185',
}
CHECKPOINT = ROOT / 'runtime/checkpoints/modnet_photographic_portrait_matting.ckpt'
REVISION_LABEL = 'org.opencontainers.image.revision'
OWNER_LABEL = 'face-algorithm.release-owner'
THREAD_ENV = (
    'OPENBLAS_NUM_THREADS', 'GOTO_NUM_THREADS', 'BLIS_NUM_THREADS',
    'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS',
    'VECLIB_MAXIMUM_THREADS', 'OPENCV_FOR_THREADS_NUM',
)
SAFE_ENV = {'FACE_ALGO_LEGACY_TOKEN', 'FACE_ALGO_CORS_ORIGINS'}
PRESENCE_BLOCK = '''
video_presence:
  mode: uncalibrated
  threshold: null
  threshold_id: null
  sample_interval_seconds: 0.5
  max_supplemental_frames: 2
  max_analyzed_frames: 1200
  max_face_candidates: 5
  max_evidence_frames: 5
  reference_min_face_size: 112
  video_min_face_size: 64
'''


class ReleaseError(RuntimeError):
    """A guard or checked external operation failed."""


def docker(*args: str, optional: bool = False, env: dict | None = None) -> str:
    result = subprocess.run(['docker', *map(str, args)], capture_output=True,
                            text=True, env=env, check=False)
    if result.returncode:
        if optional:
            return ''
        # Deliberately do not echo command arguments, stdout or stderr: env may be secret.
        raise ReleaseError(f'Docker {args[0]} failed (exit {result.returncode}); inspect locally')
    return result.stdout


def inspect(name: str, *, image: bool = False) -> dict | None:
    raw = docker('image' if image else 'container', 'inspect', name, optional=True)
    return json.loads(raw)[0] if raw else None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def full_commit(value: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{40}', value):
        raise argparse.ArgumentTypeError('--commit must be the full lowercase 40-character commit SHA')
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def protected_snapshot() -> dict:
    item = inspect(PROTECTED)
    require(bool(item) and item['Id'] == PROTECTED_ID, 'Protected 18001 container ID differs')
    require(item['State']['Running'], 'Protected 18001 container is not running')
    return {
        'Id': item['Id'], 'Image': item['Image'], 'HostConfig': item['HostConfig'],
        'StartedAt': item['State'].get('StartedAt'), 'Running': item['State']['Running'],
        'RestartCount': item.get('RestartCount'),
    }


def verify_baseline_config() -> None:
    require(OLD_CONFIG.is_file() and not OLD_CONFIG.is_symlink() and
            sha256(OLD_CONFIG) == OLD_CONFIG_SHA256, 'Approved baseline config SHA differs')


def verify_original(name: str, item: dict | None, *, running: bool) -> None:
    require(bool(item) and item['Id'] == OLD_CONTAINER_IDS[name],
            'Original container identity differs from approved baseline')
    prefix = 'face-algorithm' if name == API else 'face-algorithm-visual-console'
    require(item['Config']['Image'] == prefix + ':task-2026-001-' + OLD_REVISION,
            'Original image differs from approved baseline')
    labels = item['Config'].get('Labels') or {}
    require(labels.get(REVISION_LABEL) == OLD_COMMIT and labels.get(OWNER_LABEL) == API,
            'Original labels differ from approved baseline')
    if running:
        require(item['State']['Running'] and item['State'].get('Health', {}).get('Status') == 'healthy',
                'Original container is not running and healthy at approved baseline')
    if name == API:
        mounts = [mount for mount in item.get('Mounts', [])
                  if mount.get('Destination') == '/app/config/algorithm.yaml']
        require(len(mounts) == 1 and mounts[0].get('Source') == str(OLD_CONFIG) and
                mounts[0].get('RW') is False, 'Original config mount differs from approved baseline')


def baseline_snapshot() -> dict:
    """Read-only exact-current-baseline guard, before any deployment mutation."""
    verify_baseline_config()
    containers = {name: inspect(name) for name in (API, CONSOLE)}
    for name, item in containers.items():
        verify_original(name, item, running=True)
    return containers


def extract_archive(archive: Path, target: Path, commit: str) -> None:
    """Only regular API files/directories; reject all links and escaping names."""
    require(not target.exists(), 'Release source already exists without a completed build state')
    with tarfile.open(archive, 'r:') as bundle:
        require(bundle.pax_headers.get('comment') == commit, 'Archive commit provenance differs')
        members = []
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and '..' not in path.parts,
                    'Unsafe archive path')
            require(member.isfile() or member.isdir(), 'Archive contains a link or special file')
            if path.parts and path.parts[0] == 'FaceFeatureExtractorAPI':
                members.append(member)
        require(bool(members), 'Archive is missing FaceFeatureExtractorAPI')
        target.mkdir(mode=0o755)
        # Validated manually for compatibility with host Python versions before 3.12.
        bundle.extractall(target, members=members)


def merged_config(old: str) -> str:
    """Legacy presence migration, retained for compatibility but not this release.

    Keep every existing value/text intact. Nonstandard or ambiguous section
    syntax is refused rather than guessing without a host YAML dependency.
    The build's containerized YAML validation remains the semantic authority.
    """
    lines = old.splitlines(keepends=True)
    headers = [index for index, line in enumerate(lines)
               if re.match(r'''^(?:video_presence|['"]video_presence['"])[ \t]*:''', line)]
    if not headers:
        return old.rstrip('\r\n') + '\n' + PRESENCE_BLOCK
    require(len(headers) == 1, 'Ambiguous duplicate video_presence section')
    index = headers[0]
    require(re.fullmatch(r'video_presence:[ \t]*(?:#[^\r\n]*)?(?:\r?\n)?', lines[index]) is not None,
            'Unsupported video_presence section syntax')
    keys = set()
    for line in lines[index + 1:]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line[0].isspace():
            break
        field = re.fullmatch(r'  ([a-z_][a-z_0-9]*):(?:[ \t]+[^\r\n]*)?(?:\r?\n)?', line)
        require(field is not None, 'Unsupported video_presence field syntax')
        key = field.group(1)
        require(key not in keys, 'Ambiguous duplicate video_presence field')
        keys.add(key)
    newline = '\r\n' if lines[index].endswith('\r\n') else '\n'
    additions = ''.join('  ' + key + ': ' + value + newline for key, value in (
        ('threshold', 'null'), ('threshold_id', 'null'), ('max_supplemental_frames', '2'),
    ) if key not in keys)
    if not additions:
        return old
    if not lines[index].endswith('\n'):
        lines[index] += newline
    lines.insert(index + 1, additions)
    return ''.join(lines)


def portrait_config(old: str) -> str:
    """Change only approved portrait scalars; never merge repository defaults.

    This deliberately narrow text patch needs no host YAML package. The
    containerized strict YAML comparison below is the semantic authority.
    """
    lines = old.splitlines(keepends=True)
    headers = [index for index, line in enumerate(lines)
               if re.match(r'''^(?:portrait_template|['"]portrait_template['"])[ \t]*:''', line)]
    require(len(headers) == 1, 'Missing or ambiguous duplicate portrait_template section')
    index = headers[0]
    require(re.fullmatch(r'portrait_template:[ \t]*(?:#[^\r\n]*)?(?:\r?\n)?', lines[index]) is not None,
            'Unsupported portrait_template section syntax')
    fields = {}
    for offset in range(index + 1, len(lines)):
        line = lines[offset]
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line[0].isspace():
            break
        field = re.fullmatch(
            r'  ([a-z_][a-z_0-9]*):[ \t]+([a-zA-Z0-9_.-]+)([ \t]*(?:#[^\r\n]*)?)(\r?\n)?', line)
        if field is None:
            # The deployed template has one standard flow-list value. It is
            # preserved byte-for-byte, never parsed or rewritten on the host.
            field = re.fullmatch(
                r'  (background_rgb):[ \t]+(\[[0-9]{1,3},[ \t]*[0-9]{1,3},[ \t]*[0-9]{1,3}\])'
                r'([ \t]*(?:#[^\r\n]*)?)(\r?\n)?', line)
        require(field is not None, 'Unsupported portrait_template scalar syntax')
        key, value = field.group(1, 2)
        require(key not in fields, 'Ambiguous duplicate portrait_template field')
        fields[key] = (offset, value, field.group(3), field.group(4) or '')
    mode = fields.get('mode', (None, 'head_shoulders'))[1]
    version = fields.get('version', (None, None))[1]
    require((mode, version) in {
        ('head_shoulders', 'head-shoulders-480x640-v1'),
        ('face_archive', 'face-archive-480x640-v1'),
    }, 'Unexpected portrait_template mode or version')
    for key, value in (('mode', 'face_archive'), ('version', 'face-archive-480x640-v1')):
        if key in fields:
            offset, _, comment, newline = fields[key]
            lines[offset] = f'  {key}: {value}{comment}{newline}'
    if 'mode' not in fields:
        newline = '\r\n' if lines[index].endswith('\r\n') else '\n'
        lines.insert(index + 1, '  mode: face_archive' + newline)
    return ''.join(lines)


def image_threshold_config(old: str) -> str:
    """Narrow, comment-preserving image-only migration from the pinned baseline."""
    lines = old.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines)
               if re.match(r'''^(?:image_verification|['"]image_verification['"])[ \t]*:''', line)]
    require(len(headers) == 1, 'Missing or ambiguous image_verification section')
    index = headers[0]
    require(re.fullmatch(r'image_verification:[ \t]*(?:#[^\r\n]*)?(?:\r?\n)?', lines[index]) is not None,
            'Unsupported image_verification section syntax')
    fields = {}
    for offset in range(index + 1, len(lines)):
        line = lines[offset]
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line[0].isspace():
            break
        field = re.fullmatch(
            r'  ([a-z_][a-z_0-9]*):[ \t]+([a-z_]+)([ \t]*(?:#[^\r\n]*)?)(\r?\n)?', line)
        require(field is not None, 'Unsupported image_verification field syntax')
        key, value = field.group(1, 2)
        require(key not in fields, 'Duplicate image_verification field')
        fields[key] = (offset, value, field.group(3), field.group(4) or '')
    require({key: value[1] for key, value in fields.items()} == {
        'mode': 'uncalibrated', 'reference_quality_profile': 'entry_verify',
        'probe_quality_profile': 'entry_verify',
    }, 'Unexpected image_verification baseline')
    offset, _, comment, ending = fields['mode']
    newline = '\r\n' if lines[index].endswith('\r\n') else '\n'
    lines[offset] = '  mode: configured' + comment + (ending or newline)
    lines.insert(offset + 1, '  threshold: 0.55' + newline)
    return ''.join(lines)


def config_validation_script() -> str:
    """Strict YAML comparison: permit only the approved image trial delta."""
    return '''import copy
import json
from pathlib import Path
import yaml

class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _ in node.value:
            if key_node.tag != 'tag:yaml.org,2002:str':
                raise ValueError('Unsupported configuration mapping key')
            key = self.construct_object(key_node, deep=deep)
            if key in keys:
                raise ValueError('Duplicate configuration mapping key')
            keys.add(key)
        return super().construct_mapping(node, deep=deep)

a = yaml.load(Path('/old.yaml').read_text(), Loader=UniqueLoader)
b = yaml.load(Path('/new.yaml').read_text(), Loader=UniqueLoader)
assert a['models']['portrait_matting']['enabled'] is True
assert a['video_presence']['mode'] == 'configured'
assert type(a['video_presence']['threshold']) is float and a['video_presence']['threshold'] == 0.5
assert a['video_presence']['threshold_id'] == 'TASK-2026-001-presence-trial-050-20260928'
assert a['portrait_template']['mode'] == 'face_archive'
assert a['portrait_template']['version'] == 'face-archive-480x640-v1'
assert a['image_verification'] == {'mode': 'uncalibrated',
    'reference_quality_profile': 'entry_verify', 'probe_quality_profile': 'entry_verify'}
expected = copy.deepcopy(a)
# Replace this mapping, never mutate a potentially aliased object.
expected['image_verification'] = dict(a['image_verification'], mode='configured', threshold=0.55)
assert type(b['image_verification'].get('threshold')) is float
assert json.dumps(b, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)
'''


def port_free(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            # Linux permits reusing TIME_WAIT here, but still refuses an active
            # listener. Never use SO_REUSEPORT, which would weaken that guard.
            if sys.platform == 'linux':
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
        except OSError as error:
            raise ReleaseError(f'Required port {host}:{port} is occupied') from error


def wait_port_free(host: str, port: int, *, timeout: float = 10) -> None:
    """Bounded promotion-only wait for Docker's stopped proxy to release its port."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            port_free(host, port)
            return
        except ReleaseError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.25, remaining))


def wait_http(base: str, *, presence: bool, timeout: float = 180) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            path = '/health/ready' if presence else '/health/live'
            with opener.open(base + path, timeout=4) as response:
                require(response.status == 200, 'Readiness is not successful')
                if presence:
                    ready = json.load(response)
                    require(ready.get('ready') is True and
                            all(ready.get('capabilities', {}).get(name) == 'ready'
                                for name in ('video_presence', 'portrait')),
                            'Presence or portrait capability is not ready')
            if presence:
                with opener.open(base + '/openapi.json', timeout=4) as response:
                    schema = json.load(response)
                require('/api/v2/verify/video/presence' in schema['paths'],
                        'New presence route is absent')
                require(schema['info']['version'] == '2.4.0', 'Expected API 2.4.0 is absent')
                response = schema['paths']['/api/v2/verify/video/presence']['post']['responses']['200']
                require(response['content']['application/json']['schema']['$ref'] ==
                        '#/components/schemas/VideoPresenceResult', 'Presence response contract differs')
                models = schema['components']['schemas']
                image_op = schema['paths']['/api/v2/verify/images']['post']
                image_ref = image_op['requestBody']['content']['multipart/form-data']['schema']['$ref']
                image_upload = models[image_ref.rsplit('/', 1)[1]]
                require(set(image_upload['properties']) == {'reference_image', 'probe_image'} and
                        set(image_upload['required']) == {'reference_image', 'probe_image'},
                        'Image comparison upload contract differs')
                require(image_op['responses']['200']['content']['application/json']['schema']['$ref'] ==
                        '#/components/schemas/ImageVerificationResponse', 'Image response contract differs')
                require(set(models['ImageCalibrationSummary']['properties']['status']['enum']) ==
                        {'UNCALIBRATED', 'ACTIVE', 'CONFIGURED_UNVALIDATED'} and
                        {'IMAGE_TRIAL_THRESHOLD_MATCHED', 'IMAGE_TRIAL_THRESHOLD_NOT_MATCHED'} <=
                        set(models['ReasonCode']['enum']), 'Image trial threshold contract is absent')
                properties = models['VideoPresenceResult']['properties']
                require({
                    'comparison_status', 'threshold', 'threshold_id', 'policy_fingerprint',
                    'sampling_strategy', 'requested_sampling_interval_seconds', 'base_frames',
                    'supplemental_frames', 'compared_candidates', 'failed_candidates', 'quality_warnings',
                } <= properties.keys(), 'Best-effort presence fields are absent')
                require(set(properties['calibration_status']['enum']) ==
                        {'UNCALIBRATED', 'CONFIGURED_UNVALIDATED'}, 'Presence trial contract differs')
                require({'candidate_index', 'quality_warnings'} <=
                        models['PresenceEvidenceFrame']['properties'].keys(), 'Presence evidence fields are absent')
                portrait = schema['paths']['/api/v2/portrait/generate']['post']
                body = portrait['requestBody']
                body_ref = body['content']['multipart/form-data']['schema']['$ref']
                require(body_ref.startswith('#/components/schemas/'), 'Portrait request reference differs')
                upload = models[body_ref.rsplit('/', 1)[1]]
                image_upload = upload['properties']['image']
                require(body.get('required') is True and set(upload.get('required', [])) == {'image'} and
                        set(upload['properties']) == {'image'} and
                        image_upload.get('type') == 'string' and
                        (image_upload.get('format') == 'binary' or
                         ('format' not in image_upload and
                          image_upload.get('contentMediaType') == 'application/octet-stream')),
                        'Portrait image upload contract differs')
                require(portrait['responses']['200']['content']['application/json']['schema']['$ref'] ==
                        '#/components/schemas/PortraitResponse', 'Portrait response contract differs')
                fields = models['PortraitResponse']['properties']
                require(set(fields) == {
                    'success', 'suggestion', 'reason_codes', 'model_versions', 'config_version',
                    'processing_time_ms', 'trace_id', 'template_version', 'output_width', 'output_height',
                    'output_format', 'jpeg_quality', 'original_width', 'original_height',
                    'rotation_quarter_turns', 'placement', 'matting_quality', 'image_base64',
                }, 'Portrait response fields changed')
                require(set(models['PortraitMattingQuality']['properties']) == {
                    'face_mean', 'face_coverage', 'subject_area_ratio',
                    'shoulder_depth_ratio', 'shoulder_band_coverage',
                } and set(models['PortraitPlacement']['properties']) == {
                    'scale', 'translate_x', 'translate_y', 'affine',
                }, 'Portrait nested response fields changed')
                require(fields['reason_codes']['items'].get('$ref') == '#/components/schemas/ReasonCode' and
                        {'PORTRAIT_SOURCE_LIMITED', 'PORTRAIT_UPSCALE_LIMITED'} <=
                        set(models['ReasonCode']['enum']), 'Portrait warning reason codes are absent')
            return
        except (OSError, ValueError, KeyError, TypeError, ReleaseError):
            time.sleep(2)
    raise ReleaseError('Readiness timeout; no response bodies were logged')


def wait_restored_api(base: str, *, timeout: float = 180) -> None:
    """Verify the restored 4e6a API against its approved 2.3.1 baseline."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(base + '/health/ready', timeout=4) as response:
                require(response.status == 200, 'Restored API readiness is not successful')
                ready = json.load(response)
                require(ready.get('ready') is True and
                        all(ready.get('capabilities', {}).get(name) == 'ready'
                            for name in ('video_presence', 'portrait')),
                        'Restored presence or portrait capability is not ready')
                model_states = {model['name']: model['state'] for model in ready['models']}
                require(all(model_states.get(name) == 'ready' for name in (
                    'face_detector', 'face_landmark_primary', 'face_landmark_secondary',
                    'face_recognizer', 'rotation_classifier', 'pose_var', 'pose_conv', 'portrait_matting',
                )), 'Restored API models are not ready')
            with opener.open(base + '/openapi.json', timeout=4) as response:
                require(response.status == 200, 'Restored API schema is not available')
                require(json.load(response)['info']['version'] == '2.3.1',
                        'Restored API version differs from approved baseline')
            return
        except (OSError, ValueError, KeyError, TypeError, ReleaseError):
            time.sleep(2)
    raise ReleaseError('Restored API readiness timeout; no response bodies were logged')


class Release:
    def __init__(self, commit: str):
        self.commit = full_commit(commit)
        self.short = commit[:12]
        self.source = ROOT / ('source-' + commit) / 'FaceFeatureExtractorAPI'
        self.config = ROOT / 'runtime' / ('algorithm-' + commit + '.yaml')
        self.state_path = ROOT / ('presence-release-' + commit + '.json')
        self.candidate = API + '-candidate-' + self.short
        self.images = {
            API: 'face-algorithm:task-2026-001-' + self.short,
            CONSOLE: 'face-algorithm-visual-console:task-2026-001-' + self.short,
        }
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        require(not self.state or self.state.get('commit') == commit, 'Release state commit differs')

    def save(self, stage: str) -> None:
        self.state.update(commit=self.commit, stage=stage)
        temporary = self.state_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.state, indent=2) + '\n')
        temporary.replace(self.state_path)

    def owned(self, item: dict | None) -> bool:
        labels = (item or {}).get('Config', {}).get('Labels') or {}
        return labels.get(REVISION_LABEL) == self.commit and labels.get(OWNER_LABEL) == API

    def verify_images(self) -> None:
        for tag in self.images.values():
            item = inspect(tag, image=True)
            require(bool(item) and (item['Config'].get('Labels') or {}).get(REVISION_LABEL)
                    == self.commit, 'Required image missing or revision label differs')

    def build(self) -> None:
        if self.state.get('stage') != 'promoted':
            baseline_snapshot()
        if self.state.get('stage') in ('built', 'candidate', 'promoted', 'rolled-back'):
            self.verify_images()
            require(sha256(self.config) == self.state['config_sha256'], 'Release config changed')
            print('Existing pinned build verified')
            return
        archive = ROOT / ('release-' + self.commit + '.tar')
        require(archive.is_file(), 'Pinned git archive not found')
        require(OLD_CONFIG.is_file() and CHECKPOINT.is_file(), 'Existing runtime mounts missing')
        for tag in self.images.values():
            require(inspect(tag, image=True) is None, 'Refusing to overwrite an existing image tag')
        extract_archive(archive, self.source.parent, self.commit)
        require(sha256(self.source / 'requirements-cpu.txt') ==
                sha256(OLD_SOURCE / 'requirements-cpu.txt'), 'Offline requirements changed')
        manifest = json.loads((self.source / 'models.manifest.json').read_text())
        for model in manifest['models']:
            if model['required']:
                path = self.source / model['path']
                require(path.resolve().is_relative_to(self.source.resolve()), 'Model path escapes source')
                require(path.is_file() and sha256(path) == model['sha256'], 'Model integrity check failed')
        require(not self.config.exists(), 'Refusing to overwrite an existing runtime config')
        original_bytes = OLD_CONFIG.read_bytes()
        migrated = image_threshold_config(original_bytes.decode('utf-8')).encode('utf-8')
        with self.config.open('xb') as target:
            target.write(migrated)
        self.config.chmod(OLD_CONFIG.stat().st_mode & 0o777)
        require(sha256(OLD_CONFIG) == OLD_CONFIG_SHA256,
                'Original runtime config differs from approved baseline')
        # Use the already installed PyYAML, without depending on host Python packages.
        validation = config_validation_script()
        docker('run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges',
               '--mount', f'type=bind,src={OLD_CONFIG},dst=/old.yaml,readonly',
               '--mount', f'type=bind,src={self.config},dst=/new.yaml,readonly',
               'face-algorithm:task-2026-001-' + OLD_REVISION, 'python', '-c', validation)
        shutil.copyfile(OLD_SOURCE / 'Dockerfile.offline', self.source / 'Dockerfile.offline')
        print('Building pinned API image (offline wheelhouse)')
        docker('build', '--label', f'{REVISION_LABEL}={self.commit}',
               '--build-context', f'wheelhouse={ROOT / "build-cache/wheelhouse-6200689"}',
               '-f', str(self.source / 'Dockerfile.offline'), '-t', self.images[API], str(self.source))
        print('Building pinned visual console image')
        docker('build', '--label', f'{REVISION_LABEL}={self.commit}',
               '-t', self.images[CONSOLE], str(self.source / 'tools/visual-console'))
        self.state.update(archive_sha256=sha256(archive), config_sha256=sha256(self.config))
        self.save('built')

    def run_container(self, name: str, *, console: bool, candidate: bool = False,
                      old_api: dict | None = None) -> None:
        require(inspect(name) is None, 'Refusing to replace an existing container')
        kind = CONSOLE if console else API
        args = ['run', '-d', '--name', name, '--restart', 'unless-stopped',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--label', f'{REVISION_LABEL}={self.commit}', '--label', f'{OWNER_LABEL}={API}',
                '--log-driver', 'json-file', '--log-opt', 'max-file=3',
                '--log-opt', 'max-size=5m' if console else 'max-size=10m']
        process_env = os.environ.copy()
        if console:
            args += ['--cpus', '0.5', '--memory', '256m', '--memory-swap', '256m',
                     '--pids-limit', '128', '-p', '<server-37>:18003:8080',
                     '-e', 'VISUAL_CONSOLE_UPSTREAM=http://<server-37>:18002',
                     '-e', 'VISUAL_CONSOLE_PUBLIC_ORIGIN=http://<server-37>:18003']
        else:
            args += ['--cpus', '4', '--memory', '6g', '--memory-swap', '6g', '--pids-limit', '512',
                     '--tmpfs', '/app/tmp:rw,nosuid,nodev,noexec,size=2g,uid=10001,gid=10001,mode=0700',
                     '-p', '127.0.0.1:18012:8000' if candidate else '<server-37>:18002:8000',
                     '--mount', f'type=bind,src={self.config},dst=/app/config/algorithm.yaml,readonly',
                     '--mount', f'type=bind,src={CHECKPOINT},dst=/app/checkpoints/{CHECKPOINT.name},readonly',
                     '-e', 'OMP_WAIT_POLICY=PASSIVE']
            for key in THREAD_ENV:
                args += ['-e', key + '=1']
            for entry in (old_api or {}).get('Config', {}).get('Env', []):
                key, _, value = entry.partition('=')
                if key in SAFE_ENV:
                    process_env[key] = value
                    args += ['-e', key]  # Values are passed through process env, never argv/logs.
        docker(*args, self.images[kind], env=process_env)

    def candidate_start(self) -> None:
        self.verify_images()
        require(self.state.get('stage') in ('built', 'candidate', 'rolled-back'),
                'Build must complete before candidate')
        require(sha256(self.config) == self.state['config_sha256'], 'Release config changed')
        if self.state.get('stage') == 'rolled-back':
            originals = self.state.get('originals', {})
            require(set(originals) == {API, CONSOLE}, 'Original container identity records missing')
            for name, original_id in originals.items():
                current = inspect(name)
                require(bool(current) and current['Id'] == original_id and current['State']['Running'],
                        'Original container identity differs or is not restored and running')
        baseline = baseline_snapshot()
        existing = inspect(self.candidate)
        if existing:
            require(self.owned(existing), 'Candidate name belongs to another release')
            require(existing['State']['Running'], 'Existing candidate is stopped; inspect before retrying')
        else:
            port_free('127.0.0.1', 18012)
            self.run_container(self.candidate, console=False, candidate=True, old_api=baseline[API])
        wait_http('http://127.0.0.1:18012', presence=True)
        self.save('candidate')
        print('Candidate ready on 127.0.0.1:18012; perform acceptance before promote')

    def rollback(self) -> None:
        originals = self.state.get('originals', {})
        require(set(originals) == {API, CONSOLE}, 'No recorded original container IDs; refusing rollback')
        require(originals == OLD_CONTAINER_IDS, 'Recorded original IDs differ from approved baseline')
        verify_baseline_config()
        # Validate EVERY target before removing or renaming ANY container.
        for name, original_id in originals.items():
            current, backup = inspect(name), inspect(name + '-prev-' + OLD_REVISION)
            require(current is None or current['Id'] == original_id or self.owned(current),
                    'Current name belongs to an unrelated container; refusing rollback')
            require((current and current['Id'] == original_id) or
                    (backup and backup['Id'] == original_id), 'Original container missing; refusing rollback')
            original = current if current and current['Id'] == original_id else backup
            verify_original(name, original, running=False)
        for name in (CONSOLE, API):
            current = inspect(name)
            if current and current['Id'] != originals[name]:
                docker('rm', '-f', current['Id'])
        for name in (API, CONSOLE):
            current = inspect(name)
            if not current:
                docker('rename', originals[name], name)
            docker('start', originals[name])
        wait_restored_api('http://<server-37>:18002')
        wait_http('http://<server-37>:18003', presence=False)
        self.save('rolled-back')
        print('Original API and console restored; earlier backups retained')

    def promote(self) -> None:
        self.verify_images()
        if self.state.get('stage') == 'promoted':
            require(sha256(self.config) == self.state['config_sha256'], 'Release config changed')
            require(all(self.owned(inspect(name)) for name in (API, CONSOLE)),
                    'Promoted container identity changed')
            wait_http('http://<server-37>:18002', presence=True)
            wait_http('http://<server-37>:18003', presence=False)
            print('Release is already promoted and healthy')
            return
        require(self.state.get('stage') == 'candidate', 'Healthy candidate required before promotion')
        require(sha256(self.config) == self.state['config_sha256'], 'Release config changed')
        candidate = inspect(self.candidate)
        require(self.owned(candidate) and candidate['State']['Running'], 'Candidate identity differs')
        wait_http('http://127.0.0.1:18012', presence=True)
        originals = {}
        baseline = baseline_snapshot()
        current_api = baseline[API]
        for name, current in baseline.items():
            require(inspect(name + '-prev-' + OLD_REVISION) is None, 'Backup name already exists')
            originals[name] = current['Id']
        self.state['originals'] = originals
        self.save('promoting')  # Persist before any service is stopped; rollback recovers interrupts.
        try:
            docker('rm', '-f', candidate['Id'])
            for name in (CONSOLE, API):
                docker('stop', '--time', '30', originals[name])
                docker('rename', originals[name], name + '-prev-' + OLD_REVISION)
            wait_port_free('<server-37>', 18002)
            wait_port_free('<server-37>', 18003)
            self.run_container(API, console=False, old_api=current_api)
            wait_http('http://<server-37>:18002', presence=True)
            self.run_container(CONSOLE, console=True)
            wait_http('http://<server-37>:18003', presence=False)
            self.save('promoted')
        except BaseException:
            print('Promotion failed; restoring original containers')
            self.rollback()
            raise
        print('Pinned release promoted; original containers retained for rollback')

    def status(self) -> None:
        result = {'commit': self.commit, 'stage': self.state.get('stage', 'not-built'), 'containers': {}}
        for name in (API, CONSOLE, self.candidate, PROTECTED):
            item = inspect(name)
            result['containers'][name] = None if not item else {
                'id': item['Id'], 'image': item['Config']['Image'], 'running': item['State']['Running'],
            }
        print(json.dumps(result, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True, type=full_commit)
    parser.add_argument('--mode', required=True, choices=('build', 'candidate', 'promote', 'status', 'rollback'))
    args = parser.parse_args()
    require(os.name == 'posix' and ROOT.is_dir() and not ROOT.is_symlink(), 'Run only on the approved Linux37 deployment root')
    import fcntl
    with (ROOT / '.presence-release.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        baseline = protected_snapshot()
        try:
            release = Release(args.commit)
            action = release.candidate_start if args.mode == 'candidate' else getattr(release, args.mode)
            action()
        finally:
            require(protected_snapshot() == baseline, 'Protected 18001 container state changed during release')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ReleaseError, OSError, ValueError) as error:
        print(f'Release stopped safely: {type(error).__name__}: {error}')
        raise SystemExit(1)
