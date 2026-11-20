"""Observe the reported version: published v0.3.1 wheel vs the v0.3.2 candidate."""
import email
import os
from pathlib import Path
import subprocess
import tarfile
import tomllib
import zipfile

candidate = tomllib.loads(Path('orchestrator/pyproject.toml').read_text())['project']['version']
lock = tomllib.loads(Path('orchestrator/uv.lock').read_text())
locked = next(p['version'] for p in lock['package'] if p['name'] == 'neural-amplifier')
assert candidate == locked == '0.3.2', (candidate, locked)


def reported(wheel):
    """Run the wheel (with its dependencies) in a fresh isolated uv environment."""
    code = 'import neural_amplifier as n; print(n.__version__)'
    return subprocess.run(['uv', 'run', '-q', '--no-project', '--isolated', '--with', str(wheel),
                           'python', '-I', '-c', code],
                          capture_output=True, text=True, check=True).stdout.strip()


old_wheel, = Path(os.environ['RELEASE_DEMO_OLD']).glob('*.whl')
root = Path(os.environ['RELEASE_DEMO_DIST'])
wheel, = root.glob('*.whl')
sdist, = root.glob('*.tar.gz')
print('Published v0.3.1 wheel reports __version__ =', reported(old_wheel), flush=True)
with zipfile.ZipFile(wheel) as z:
    name, = (n for n in z.namelist() if n.endswith('.dist-info/METADATA'))
    wheel_version = email.message_from_bytes(z.read(name))['Version']
with tarfile.open(sdist) as t:
    member, = (m for m in t.getmembers() if m.name.endswith('/PKG-INFO'))
    sdist_version = email.message_from_bytes(t.extractfile(member).read())['Version']
assert wheel_version == sdist_version == candidate
new = reported(wheel)
print('Candidate v0.3.2 wheel reports __version__ =', new, flush=True)
assert new == '0.3.2'
print('Lock, wheel METADATA and sdist PKG-INFO: 0.3.2. Candidate artifacts only; no publication.', flush=True)
