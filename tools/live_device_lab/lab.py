#!/usr/bin/env python3
"""DreamJourney USB device lab. Default operations are local; run is explicit."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import wave

HERE = Path(__file__).resolve().parent
DEFAULT_IOS = Path('/Users/gaominge/Documents/Codex/Video/DreamJourney_dev')
BUNDLE = 'com.gaominge.dreamjourney.app'
RUN_RE = re.compile(r'^lab-[0-9a-f]{32}$')


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def tool_fingerprint():
    values = {str(p.relative_to(HERE)): digest(p) for p in sorted(HERE.rglob('*'))
              if p.is_file() and p.suffix in ('.py', '.swift')}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def bundle_fingerprint(app):
    # Debug's small launcher does not contain the real app code. Bind the debug
    # dylib, embedded frameworks, resources and signing material as well.
    app = Path(app).resolve()
    if not (app / 'DreamJourney').is_file():
        raise ValueError('app executable missing')
    values = {}
    for p in sorted(app.rglob('*')):
        name = str(p.relative_to(app))
        if p.is_symlink():
            if not p.resolve().is_relative_to(app):
                raise ValueError('app bundle symlink escaped bundle')
            values[name] = {'symlink': os.readlink(p)}
        elif p.is_file():
            values[name] = {'sha256': digest(p)}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def execution_identity(identity):
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def call(args, *, log=None, timeout=90):
    # No shell, credentials, shell interpolation or raw app console collection.
    if log:
        with open(log, 'w') as f:
            subprocess.run(list(map(str, args)), stdout=f, stderr=subprocess.STDOUT,
                           timeout=timeout, check=True)
    else:
        subprocess.run(list(map(str, args)), timeout=timeout, check=True)


def validate_manifest(m, root):
    if m.get('schema') != 1 or not RUN_RE.fullmatch(m.get('runID', '')):
        raise ValueError('invalid run identity')
    profiles = {'short': (2, 0, 300), '10m': (32, 600, 900), '20m': (110, 1200, 3600), '40m': (220, 2400, 7200)}
    if m.get('profile') not in profiles:
        raise ValueError('unsupported profile')
    count, duration, maximum = profiles[m['profile']]
    if len(m['turns']) != count or m['minimumDurationSeconds'] != duration:
        raise ValueError('profile turn count or duration changed')
    if not 60 <= m['maximumDurationSeconds'] <= maximum:
        raise ValueError('runtime budget exceeded')
    if m.get('sampleRate') != 16000 or m.get('channels') != 1 or m.get('sampleBytes') != 2:
        raise ValueError('requires 16 kHz mono signed 16-bit little-endian PCM')
    if not m.get('requiredMemoryTerms') or m.get('maxCandidateWrites', 0) > 200:
        raise ValueError('missing memory assertions or excessive write budget')
    for i, t in enumerate(m['turns']):
        if t.get('ordinal') != i + 1 or not t.get('requiredASRTerms'):
            raise ValueError('missing turn identity/assertions')
        if not re.fullmatch(r'turn-[0-9]{3}\.pcm', t.get('file', '')):
            raise ValueError('invalid PCM filename')
        p = Path(root) / t['file']
        if p.is_symlink() or p.parent.resolve() != Path(root).resolve():
            raise ValueError('audio path escaped run directory')
        data = p.read_bytes()
        if len(data) % 2 or not 3200 <= len(data) <= 16000 * 2 * 40:
            raise ValueError('invalid PCM size')
        if hashlib.sha256(data).hexdigest() != t['sha256']:
            raise ValueError('audio digest changed')
        if not any(data):
            raise ValueError('silent synthetic speech')
    return m


def prepare(root, profile, voice):
    root = Path(root).resolve()
    if root.exists():
        raise ValueError('refusing to replace an existing run')
    root.mkdir(parents=True)
    nonce = uuid.uuid4().hex
    run_id = 'lab-' + nonce
    # Known synthetic facts: never copy a prior conversation or feed AI answers back as user facts.
    # The spoken numeric name is bound to the device-created Source, not used as the sole write guard.
    digits = ''.join('零一二三四五六七八九'[int(c, 16) % 10] for c in nonce[:6])
    marker = '星桥' + digits
    short = [
        (f'这次我新布置了另一个独立的读书角，和之前聊过的读书角不是同一处，也不是旧场所改名。这个新读书角叫{marker}，名字的星是星星的星，桥是桥梁的桥。我每周六上午在那里读书。请简短回应。', [marker, '周六']),
        (f'补充一下，这次新建的{marker}读书角里摆着一张蓝色木桌，我最喜欢在那里读旅行故事。请简短回应。', ['蓝色', '旅行']),
    ]
    count, minimum, maximum = {'short': (2, 0, 300), '10m': (32, 600, 900), '20m': (110, 1200, 3600), '40m': (220, 2400, 7200)}[profile]
    scripts = list(short)
    activities = [('画画', '绿色'), ('写日记', '橙色'), ('做手工', '紫色'), ('听音乐', '白色')]
    for i in range(2, count):
        activity, color = activities[(i - 2) % len(activities)]
        text = f'关于这次新建的{marker}读书角，我再说一点。我也喜欢在那里{activity}，旁边有{color}的书签。请用一句话回应。'
        scripts.append((text, [activity, color]))
    if count > 2 and profile != '10m':
        scripts[-1] = (f'最后补充这次新建的{marker}读书角的情况。那张蓝色木桌是我自己做的，我很珍惜它。请简短回应。', ['蓝色', '自己做'])
    required_memory = [marker, '周六', '蓝色', '旅行'] + (['自己做'] if count > 2 and profile != '10m' else [])
    m = dict(schema=1, runID=run_id, profile=profile, marker=marker,
             sampleRate=16000, channels=1, sampleBytes=2,
             minimumDurationSeconds=minimum, maximumDurationSeconds=maximum,
             turnTimeoutSeconds=90, organizationTimeoutSeconds=1200,
             maxCandidateWrites=30 if profile == 'short' else 200,
             requiredMemoryTerms=required_memory, turns=[])
    for i, (text, terms) in enumerate(scripts, 1):
        aiff, wav = root / 'speech.aiff', root / 'speech.wav'
        call(['/usr/bin/say', '-v', voice, '-r', '190', '-o', aiff, text], timeout=90)
        call(['/usr/bin/afconvert', aiff, wav, '-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1'])
        with wave.open(str(wav), 'rb') as f:
            if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (16000, 1, 2):
                raise ValueError('unexpected speech conversion format')
            pcm = f.readframes(f.getnframes())
        p = root / f'turn-{i:03d}.pcm'
        p.write_bytes(pcm)
        m['turns'].append(dict(ordinal=i, text=text, file=p.name, sha256=digest(p),
                               requiredASRTerms=terms))
        aiff.unlink(); wav.unlink()
    validate_manifest(m, root)
    save(root / 'manifest.json', m)
    print(json.dumps({'prepared': run_id, 'turns': count, 'provider': 'NOT_RUN', 'device': 'NOT_RUN'}))


def fingerprint(repo):
    paths = []
    for sub, pattern in [('DreamJourney', '*.swift'), ('DreamJourneyTests', '*.swift')]:
        paths += list((repo / sub).rglob(pattern))
    paths += [repo / 'DreamJourney.xcodeproj/project.pbxproj', repo / 'Podfile.lock']
    # Every production source, project, resources and runtime config affects the receipt.
    paths += [p for p in (repo / 'DreamJourney').rglob('*') if p.is_file() and p.suffix != '.swift']
    values = {str(p.relative_to(repo)): digest(p) for p in sorted(set(paths)) if p.is_file()}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def stage(repo, dest):
    from stage import stage_workspace
    repo, dest = Path(repo).resolve(), Path(dest).resolve()
    if dest.exists():
        raise ValueError('stage destination exists; use a new directory')
    before = fingerprint(repo)
    stage_workspace(repo, dest)
    if fingerprint(repo) != before:
        raise ValueError('source changed during staging')
    save(dest / 'lab-build.json', {'schema': 2, 'source': before, 'tool': tool_fingerprint(),
                                 'stagedSource': fingerprint(dest), 'repository': str(repo)})
    print('Isolated test workspace: ' + str(dest))


def validate_stage(stage_dir):
    identity = json.loads((stage_dir/'lab-build.json').read_text())
    if identity.get('schema') != 2:
        raise ValueError('legacy build identity; create a new stage, do not rewrite old evidence')
    if tool_fingerprint() != identity.get('tool'):
        raise ValueError('tool changed; create a new stage and rebuild')
    if fingerprint(Path(identity['repository'])) != identity['source']:
        raise ValueError('source changed; rebuild before testing')
    if fingerprint(stage_dir) != identity.get('stagedSource'):
        raise ValueError('staged source changed; create a new stage and rebuild')
    return identity


def validate_build(stage_dir):
    identity = validate_stage(stage_dir)
    binary = json.loads((stage_dir/'binary.json').read_text())
    if binary.get('schema') != 2 or not binary.get('signed'):
        raise ValueError('requires a signed build with complete app bundle identity')
    app = stage_dir/'DerivedData/Build/Products/Debug-iphoneos/DreamJourney.app'
    if bundle_fingerprint(app) != binary.get('bundle'):
        raise ValueError('built app bundle changed')
    return dict(identity, **binary), app


def build(stage_dir, signed=False):
    p = Path(stage_dir).resolve()
    validate_stage(p)
    args = ['xcodebuild', '-workspace', p/'DreamJourney.xcworkspace', '-scheme', 'DreamJourney',
            '-configuration', 'Debug', '-destination', 'generic/platform=iOS',
            '-derivedDataPath', p/'DerivedData',
            'SWIFT_ACTIVE_COMPILATION_CONDITIONS=DEBUG LIVE_DEVICE_AUTOMATION',
            'COMPILER_INDEX_STORE_ENABLE=NO']
    if not signed:
        args += ['CODE_SIGNING_ALLOWED=NO']
    args += ['build']
    call(args, log=p/'build.log', timeout=2400)
    validate_stage(p)
    app = p/'DerivedData/Build/Products/Debug-iphoneos/DreamJourney.app'
    save(p/'binary.json', {'schema': 2, 'executable': digest(app/'DreamJourney'),
                          'bundle': bundle_fingerprint(app), 'signed': signed})
    print('BUILD_PASS ' + str(app))


def validate_receipt(receipt, identity, device, account_hash):
    if receipt.get('status') != 'PASS' or receipt.get('profile') != 'short':
        raise ValueError('long test requires a successful physical short test')
    if receipt.get('identity') != identity or receipt.get('device') != device:
        raise ValueError('short receipt belongs to another source, tool, binary or device')
    if receipt.get('accountHash') != account_hash or not account_hash:
        raise ValueError('short receipt account mismatch')
    if not 0 <= time.time() - receipt.get('createdAt', 0) <= 3600 or receipt.get('consumed'):
        raise ValueError('short receipt expired or consumed')


def copy_from(device, source, dest):
    call(['xcrun', 'devicectl', 'device', 'copy', 'from', '--device', device,
          '--domain-type', 'appDataContainer', '--domain-identifier', BUNDLE,
          '--source', source, '--destination', dest, '--quiet'], timeout=25)


def wait_result(device, remote, local, limit, expected_mode, expected_launch):
    until = time.monotonic() + limit
    last = None
    while time.monotonic() < until:
        try:
            copy_from(device, remote + '/result.json', local)
            value = json.loads(Path(local).read_text())
            if value.get('mode') != expected_mode or value.get('launchID') != expected_launch:
                time.sleep(2)
                continue
            progress = (value.get('stage'), value.get('completedTurns'))
            if progress != last:
                last = progress
                print(json.dumps({k: value.get(k) for k in ('stage','status','completedTurns','failure')}, ensure_ascii=False), flush=True)
            if value.get('stage') == 'awaitingSourceBinding':
                from recovery_binding import approve_scene_source
                approve_scene_source(device, remote, Path(local).parent, value)
            if value.get('status') in ('PASS', 'FAIL'):
                return value
        except __import__('binding_contract').BindingError as error:
            save(Path(local).parent/'binding-host-error.json', {'code': error.code, 'readOnly': True})
            raise
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as error:
            save(Path(local).parent/'last-read-error.json', {'category': type(error).__name__, 'readOnly': True})
        time.sleep(5)
    raise TimeoutError('device report timeout; no automatic new Live or business replay')


def run(args):
    if not args.current_test_account:
        raise ValueError('run requires --current-test-account; scoped synthetic review writes are real')
    p, stage_dir = Path(args.run).resolve(), Path(args.stage).resolve()
    m = validate_manifest(json.loads((p/'manifest.json').read_text()), p)
    if (p/'launched.json').exists():
        raise ValueError('run already launched; collect evidence instead of replaying')
    identity, app = validate_build(stage_dir)
    # Reject a missing/stale short gate BEFORE installing, copying or launching
    # anything on the user's phone. The actual account is checked on device.
    receipt_path = None
    if m['profile'] != 'short':
        if not args.short_receipt:
            raise ValueError('missing --short-receipt')
        receipt_path = Path(args.short_receipt)
        receipt = json.loads(receipt_path.read_text())
        validate_receipt(receipt, identity, args.device, receipt.get('accountHash'))
    save(p/'identity.json', identity)
    # Install updates only the app binary; no uninstall/data deletion/production deploy.
    call(['xcrun', 'devicectl', 'device', 'install', 'app', '--device', args.device, app,
          '--json-output', p/'install.json'], timeout=180)
    remote = 'Documents/LiveDeviceLab/' + m['runID']
    call(['xcrun', 'devicectl', 'device', 'copy', 'to', '--device', args.device,
          '--domain-type', 'appDataContainer', '--domain-identifier', BUNDLE,
          '--source', p, '--destination', remote, '--remove-existing-content', 'false'], timeout=180)
    env = {'DJ_LIVE_DEVICE_LAB_RUN': m['runID'], 'DJ_LIVE_DEVICE_LAB_ALLOW_AUDIBLE': '1' if args.allow_audible else '0',
           'DJ_LIVE_DEVICE_LAB_DEVICE': args.device, 'DJ_LIVE_DEVICE_LAB_BUILD': execution_identity(identity)}
    def launch(mode):
        env['DJ_LIVE_DEVICE_LAB_MODE'] = mode
        env['DJ_LIVE_DEVICE_LAB_LAUNCH'] = uuid.uuid4().hex
        call(['xcrun', 'devicectl', 'device', 'process', 'launch', '--device', args.device,
              '--terminate-existing', '--environment-variables', json.dumps(env),
              '--json-output', p/('launch-' + mode + '.json'), BUNDLE])
        return env['DJ_LIVE_DEVICE_LAB_LAUNCH']
    # Runtime performs the complete preflight in this same process. Do not cold launch
    # twice in quick succession: that also repeats normal app startup restoration traffic.
    if receipt_path:
        receipt = json.loads(receipt_path.read_text())
        validate_receipt(receipt, identity, args.device, receipt.get('accountHash'))
        # Actual device account is checked by runtime BEFORE it can start Live.
        env['DJ_LIVE_DEVICE_LAB_EXPECTED_ACCOUNT'] = receipt['accountHash']
        receipt['consumed'] = m['runID']
        save(receipt_path, receipt)
    # Durable launch marker: a failed/disconnected command never creates a fresh business attempt.
    save(p/'launched.json', {'runID': m['runID'], 'at': time.time()})
    launch_id = launch('run')
    result = wait_result(args.device, remote, p/'result.json', m['maximumDurationSeconds'] + m['organizationTimeoutSeconds'] + 240, 'run', launch_id)
    if result['status'] == 'FAIL' and result.get('liveStarted'):
        try:
            copy_from(args.device, remote + '/diagnostic-events.json', p/'diagnostic-events.json')
        except (subprocess.SubprocessError, OSError):
            pass
    original_result = result
    if result['status'] == 'PASS' or result.get('memoryConfirmationStatus') == 'PASS':
        launch_id = launch('readback')
        # Readback is GET-only and must be produced by the new process.
        result = wait_result(args.device, remote, p/'cold-readback.json', 180, 'readback', launch_id)
        if result.get('stage') != 'coldReadback' or result.get('status') != 'PASS':
            raise ValueError('cold readback did not pass')
        if original_result['status'] == 'FAIL':
            original_result['memoryColdReadbackStatus'] = 'PASS'
            save(p/'recovery-result.json', original_result)
            result = original_result
        if m['profile'] == 'short' and result['status'] == 'PASS':
            save(p/'short-receipt.json', dict(status='PASS', profile='short', identity=identity,
                 device=args.device, accountHash=result.get('accountHash'), createdAt=time.time(), consumed=False))
    if result.get('status') != 'PASS':
        raise ValueError('real device test failed; evidence retained; long test must not start')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare'); p.add_argument('run'); p.add_argument('--profile', choices=['short','10m','20m','40m'], default='short'); p.add_argument('--voice', default='Tingting')
    p = sub.add_parser('stage'); p.add_argument('destination'); p.add_argument('--ios', default=str(DEFAULT_IOS))
    p = sub.add_parser('build'); p.add_argument('stage'); p.add_argument('--signed', action='store_true')
    p = sub.add_parser('run'); p.add_argument('run'); p.add_argument('--stage', required=True); p.add_argument('--device', required=True); p.add_argument('--current-test-account', action='store_true'); p.add_argument('--short-receipt'); p.add_argument('--allow-audible', action='store_true', help='Explicit user waiver of the muted-output precondition; real playback remains enabled')
    args = parser.parse_args()
    if args.cmd == 'prepare': prepare(args.run, args.profile, args.voice)
    elif args.cmd == 'stage': stage(args.ios, args.destination)
    elif args.cmd == 'build': build(args.stage, args.signed)
    elif args.cmd == 'run': run(args)


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, subprocess.SubprocessError, TimeoutError) as e:
        print('STOP: ' + str(e), file=sys.stderr)
        sys.exit(1)
