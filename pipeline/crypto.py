"""Password-protect the published data so leaguemates who find the site can't read your trade plans.

Format matches the browser's WebCrypto: PBKDF2-SHA256 -> AES-256-GCM over gzip(JSON).
"""
import base64
import gzip
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 200_000


def encrypt(payload: str, password: str) -> str:
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, gzip.compress(payload.encode()), None)
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return json.dumps({"v": 1, "iter": ITERATIONS, "salt": b(salt), "iv": b(iv), "ct": b(ct)})


def decrypt(blob: str, password: str) -> str:
    d = json.loads(blob)
    u = base64.b64decode
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=u(d["salt"]), iterations=d["iter"]).derive(password.encode())
    return gzip.decompress(AESGCM(key).decrypt(u(d["iv"]), u(d["ct"]), None)).decode()
