"""Sealing for the Rhetoric Search data: the Interactive Deterrence "rhetoric" password tier (t5).

Vendored from tsm-strait-layers scripts/build_site.py (derive_key, seal, seal_file, unseal_file, tier_key) so the
nightly CI job can seal without the private site repo. The format must stay byte-identical to build_site.py, which
shared/js/gate.js decrypts in the browser (tests/test_publish.py checks both):

    sealed file = magic + base64(iv || AES-256-GCM(gzip(data)))     magic = b"TSMVAULT6:" for the rhetoric tier
    key         = PBKDF2-SHA256(password, salt, 600,000 iterations), salt = base64 in vault_salt_deterrence_rhet.txt
    iv          = HMAC-SHA256(key, gzip(data))[:12]   (deterministic: unchanged files give identical bytes)

Password: env RHETORIC_TIER_PASSWORD (the CI secret), else env DETERRENCE_RHET_SITE_PASSWORD (build_site.py's name),
else the macOS keychain item interactive-deterrence-rhetoric (account tsm). The salt is not secret.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import os
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

HERE = Path(__file__).resolve().parent
ITERATIONS = 600_000
MAGIC = b"TSMVAULT2:"   # tier 1 (only for its length: every magic prefix is 10 bytes)
MAGIC5 = b"TSMVAULT6:"  # fifth tier = Rhetoric Search
SALT_FILE = HERE / "vault_salt_deterrence_rhet.txt"
KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT = "interactive-deterrence-rhetoric", "tsm"
PASSWORD_ENVS = ("RHETORIC_TIER_PASSWORD", "DETERRENCE_RHET_SITE_PASSWORD")


def tier_password() -> str:
    for name in PASSWORD_ENVS:
        if os.environ.get(name):
            return os.environ[name]
    out = subprocess.run(["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w"],
                         capture_output=True, text=True) if Path("/usr/bin/security").exists() else None
    if out is None or out.returncode != 0:
        raise SystemExit(f"No rhetoric-tier password: set {PASSWORD_ENVS[0]} or add keychain item {KEYCHAIN_SERVICE}")
    return out.stdout.strip()


def derive_key(password: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(password.encode())


def tier_key(site: str = "deterrence", tier: str = "t5") -> tuple[bytes, bytes]:
    """(AES key, magic) for the Rhetoric Search tier. Same signature as build_site.tier_key; only deterrence/t5."""
    if (site, tier) != ("deterrence", "t5"):
        raise ValueError("publish/vault.py only knows the Rhetoric Search tier (deterrence, t5)")
    salt = base64.b64decode(SALT_FILE.read_text().strip())
    return derive_key(tier_password(), salt), MAGIC5


def seal(key: bytes, data: bytes, compress: bool = False) -> str:
    """AES-256-GCM encrypt (optionally gzip first). Output is base64(iv || ciphertext)."""
    if compress:
        data = gzip.compress(data, compresslevel=9, mtime=0)
    iv = hmac.new(key, data, hashlib.sha256).digest()[:12]
    return base64.b64encode(iv + AESGCM(key).encrypt(iv, data, None)).decode()


def seal_file(key: bytes, magic: bytes, data: bytes) -> bytes:
    """A sealed data file as published: magic + base64(iv || AES-GCM(gzip(data))). gate.js decrypts it in fetch()."""
    return magic + seal(key, data, compress=True).encode()


def unseal_file(key: bytes, blob: bytes) -> bytes:
    """Inverse of seal_file (tests and tooling). Every magic prefix is 10 bytes."""
    raw = base64.b64decode(blob[len(MAGIC):])
    return gzip.decompress(AESGCM(key).decrypt(raw[:12], raw[12:], None))
