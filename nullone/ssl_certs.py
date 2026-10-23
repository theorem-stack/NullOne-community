from __future__ import annotations

import os


def ensure_ssl_certificates() -> None:
    """Use certifi CA bundle when SSL_CERT_FILE is unset (common on macOS Python)."""
    if os.getenv("SSL_CERT_FILE"):
        return
    try:
        import certifi
    except ImportError:
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()
