"""Observe release metadata on the old tag, candidate and built distributions."""
import email
import os
from pathlib import Path
import subprocess
import tarfile
import tomllib
import zipfile

base = subprocess.run(['git', 'show', 'v0.3.0:orchestrator/pyproject.toml'],
                      capture_output=True, text=True, check=True).stdout
candidate = Path('orchestrator/pyproject.toml').read_text()
old = tomllib.loads(base)['project']['version']
new = tomllib.loads(candidate)['project']['version']
lock = tomllib.loads(Path('orchestrator/uv.lock').read_text())
locked = next(p['version'] for p in lock['package'] if p['name'] == 'neural-amplifier')
print('Same package-version read: published tag', old, '-> candidate', new, flush=True)
assert old == '0.3.0' and new == locked == '0.3.1'
root = Path(os.environ['RELEASE_DEMO_DIST'])
wheel, = root.glob('*.whl')
sdist, = root.glob('*.tar.gz')
with zipfile.ZipFile(wheel) as z:
    name, = (n for n in z.namelist() if n.endswith('.dist-info/METADATA'))
    wheel_version = email.message_from_bytes(z.read(name))['Version']
with tarfile.open(sdist) as t:
    member, = (m for m in t.getmembers() if m.name.endswith('/PKG-INFO'))
    sdist_version = email.message_from_bytes(t.extractfile(member).read())['Version']
assert wheel_version == sdist_version == new
print('Lock, built wheel METADATA and sdist PKG-INFO:', new, flush=True)
print('Candidate artifacts only; no tag publication or runtime installation.', flush=True)
