from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def load_private_key(*, path: str | None = None, pem: str | None = None) -> rsa.RSAPrivateKey:
    if pem:
        key_data = pem.encode("utf-8")
    elif path:
        key_data = Path(path).read_bytes()
    else:
        raise ValueError("Either private key path or PEM must be provided")
    key = serialization.load_pem_private_key(key_data, password=None, backend=default_backend())
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("Expected RSA private key")
    return key


def sign_request(
    private_key: rsa.RSAPrivateKey,
    *,
    timestamp_ms: str,
    method: str,
    path: str,
) -> str:
    path_without_query = path.split("?")[0]
    message = f"{timestamp_ms}{method.upper()}{path_without_query}".encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def auth_headers(
    private_key: rsa.RSAPrivateKey,
    api_key: str,
    *,
    method: str,
    path: str,
    timestamp_ms: str | None = None,
) -> dict[str, str]:
    ts = timestamp_ms or str(int(time.time() * 1000))
    signature = sign_request(private_key, timestamp_ms=ts, method=method, path=path)
    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "Content-Type": "application/json",
    }
