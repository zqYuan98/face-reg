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
OLD_COMMIT = '1e6b57b5b1dd32693497c21a0ce0039b4f77fc1b'
OLD_REVISION = OLD_COMMIT[:12]
OLD_SOURCE = ROOT / ('source-' + OLD_COMMIT) / 'FaceFeatureExtractorAPI'
OLD_CONFIG = ROOT / 'runtime' / ('algorithm-' + OLD_COMMIT + '.yaml')
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
    """Patch only new scalar defaults in the approved plain YAML section.

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
                            ready.get('capabilities', {}).get('video_presence') == 'ready',
                            'Presence capability is not ready')
            if presence:
                with opener.open(base + '/openapi.json', timeout=4) as response:
                    schema = json.load(response)
                require('/api/v2/verify/video/presence' in schema['paths'],
                        'New presence route is absent')
                require(schema['info']['version'] == '2.2.0', 'Expected API 2.2.0 is absent')
                response = schema['paths']['/api/v2/verify/video/presence']['post']['responses']['200']
                require(response['content']['application/json']['schema']['$ref'] ==
                        '#/components/schemas/VideoPresenceResult', 'Presence response contract differs')
                models = schema['components']['schemas']
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
            return
        except (OSError, ValueError, KeyError, ReleaseError):
            time.sleep(2)
    raise ReleaseError('Readiness timeout; no response bodies were logged')


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
        old_text = OLD_CONFIG.read_text(encoding='utf-8')
        require(not self.config.exists(), 'Refusing to overwrite an existing runtime config')
        with self.config.open('x', encoding='utf-8', newline='') as stream:
            stream.write(merged_config(old_text))
        self.config.chmod(OLD_CONFIG.stat().st_mode & 0o777)
        # Use the already installed PyYAML, without depending on host Python packages.
        validation = (
            "import yaml; from pathlib import Path; "
            "a=yaml.safe_load(Path('/old.yaml').read_text()); "
            "b=yaml.safe_load(Path('/new.yaml').read_text()); "
            "s=yaml.safe_load(Path('/source.yaml').read_text()); "
            "assert a['models']['portrait_matting']['enabled'] is True; "
            "assert b['video_presence']==s['video_presence']; "
            "a.pop('video_presence',None); b.pop('video_presence',None); assert a==b"
        )
        docker('run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges',
               '--mount', f'type=bind,src={OLD_CONFIG},dst=/old.yaml,readonly',
               '--mount', f'type=bind,src={self.config},dst=/new.yaml,readonly',
               '--mount', f'type=bind,src={self.source / "config/algorithm.yaml"},dst=/source.yaml,readonly',
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
        existing = inspect(self.candidate)
        if existing:
            require(self.owned(existing), 'Candidate name belongs to another release')
            require(existing['State']['Running'], 'Existing candidate is stopped; inspect before retrying')
        else:
            port_free('127.0.0.1', 18012)
            current = inspect(API)
            require(bool(current), 'Current API not found')
            self.run_container(self.candidate, console=False, candidate=True, old_api=current)
        wait_http('http://127.0.0.1:18012', presence=True)
        self.save('candidate')
        print('Candidate ready on 127.0.0.1:18012; perform acceptance before promote')

    def rollback(self) -> None:
        originals = self.state.get('originals', {})
        require(set(originals) == {API, CONSOLE}, 'No recorded original container IDs; refusing rollback')
        # Validate EVERY target before removing or renaming ANY container.
        for name, original_id in originals.items():
            current, backup = inspect(name), inspect(name + '-prev-' + OLD_REVISION)
            require(current is None or current['Id'] == original_id or self.owned(current),
                    'Current name belongs to an unrelated container; refusing rollback')
            require((current and current['Id'] == original_id) or
                    (backup and backup['Id'] == original_id), 'Original container missing; refusing rollback')
        for name in (CONSOLE, API):
            current = inspect(name)
            if current and current['Id'] != originals[name]:
                docker('rm', '-f', current['Id'])
        for name in (API, CONSOLE):
            current = inspect(name)
            if not current:
                docker('rename', originals[name], name)
            docker('start', originals[name])
        self.save('rolled-back')
        wait_http('http://<server-37>:18002', presence=False)
        wait_http('http://<server-37>:18003', presence=False)
        print('Original API and console restored; earlier backups retained')

    def promote(self) -> None:
        self.verify_images()
        if self.state.get('stage') == 'promoted':
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
        current_api = inspect(API)
        for name, image_prefix in ((API, 'face-algorithm'), (CONSOLE, 'face-algorithm-visual-console')):
            current = inspect(name)
            require(bool(current) and current['State']['Running'], 'Original container missing or stopped')
            require(current['Config']['Image'] == image_prefix + ':task-2026-001-' + OLD_REVISION,
                    'Original image differs from approved baseline')
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
