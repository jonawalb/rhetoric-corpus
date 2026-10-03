"""publish/vault.py seals byte-for-byte like tsm-strait-layers scripts/build_site.py, and what it seals opens with the
browser gate's algorithm (shared/js/gate.js: PBKDF2-SHA256 600k -> AES-GCM decrypt -> gunzip), run here in Node."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "publish"))
import vault  # noqa: E402

SITE_SCRIPTS = Path.home() / "Projects" / "tsm-strait-layers" / "scripts"
KEY = bytes(range(32))
DATA = json.dumps({"build": "20260101T000000Z", "text": "Заявление МИД 外交部 — ✓"}, ensure_ascii=False).encode()


def test_round_trip_and_determinism():
    blob = vault.seal_file(KEY, vault.MAGIC5, DATA)
    assert blob.startswith(b"TSMVAULT6:") and vault.unseal_file(KEY, blob) == DATA
    assert blob == vault.seal_file(KEY, vault.MAGIC5, DATA) != vault.seal_file(KEY, vault.MAGIC5, DATA + b" ")


def test_tier_key_from_env(monkeypatch):
    monkeypatch.setenv("RHETORIC_TIER_PASSWORD", "test-password")
    key, magic = vault.tier_key("deterrence", "t5")
    salt = base64.b64decode(vault.SALT_FILE.read_text().strip())
    assert magic == vault.MAGIC5 and key == vault.derive_key("test-password", salt) and len(key) == 32
    with pytest.raises(ValueError):
        vault.tier_key("tsm", "t1")


@pytest.mark.skipif(not (SITE_SCRIPTS / "build_site.py").exists(), reason="tsm-strait-layers not checked out")
def test_byte_identical_to_site_build(monkeypatch):
    monkeypatch.syspath_prepend(str(SITE_SCRIPTS))
    import build_site
    assert build_site.seal_file(KEY, build_site.MAGIC5, DATA) == vault.seal_file(KEY, vault.MAGIC5, DATA)
    assert build_site.MAGIC5 == vault.MAGIC5 and build_site.ITERATIONS == vault.ITERATIONS
    site_salt = SITE_SCRIPTS / build_site.SITES["deterrence"]["t5"]["salt"]
    assert site_salt.read_text().strip() == vault.SALT_FILE.read_text().strip()
    assert build_site.SITES["deterrence"]["t5"]["keychain"] == vault.KEYCHAIN_SERVICE
    assert build_site.derive_key("pw", b"salt-bytes-16..!") == vault.derive_key("pw", b"salt-bytes-16..!")


GATE_JS = r"""
const [pw, saltB64, iter, blob] = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const s = crypto.subtle;
(async () => {
  const base = await s.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveBits']);
  const bits = await s.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: Buffer.from(saltB64, 'base64'), iterations: iter }, base, 256);
  const key = await s.importKey('raw', bits, 'AES-GCM', false, ['decrypt']);
  const bytes = Buffer.from(blob.slice(10), 'base64');           // drop the 10-byte magic, as gate.js does
  const plain = new Uint8Array(await s.decrypt({ name: 'AES-GCM', iv: bytes.subarray(0, 12) }, key, bytes.subarray(12)));
  const out = await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  process.stdout.write(Buffer.from(out).toString('base64'));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_gate_algorithm_opens_vault_output(monkeypatch):
    monkeypatch.setenv("RHETORIC_TIER_PASSWORD", "gate-test-password")
    key, magic = vault.tier_key()
    blob = vault.seal_file(key, magic, DATA).decode()
    salt = vault.SALT_FILE.read_text().strip()
    out = subprocess.run(["node", "-e", GATE_JS], input=json.dumps(["gate-test-password", salt, vault.ITERATIONS, blob]),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert base64.b64decode(out.stdout) == DATA
