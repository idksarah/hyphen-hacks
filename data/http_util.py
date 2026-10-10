"""Small HTTP helpers shared by the data build scripts. Standard library only.

python.org macOS installs ship without a CA bundle until "Install
Certificates.command" is run. We fall back to the system bundle, and if a
host's root is missing from that too, to the system `curl` (which still
verifies certificates).
"""

import json
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "safe-light-constellations/0.1 (hackathon project; github.com/idksarah/hyphen-hacks)"


def _ssl_context():
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and Path("/etc/ssl/cert.pem").exists():
        ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ctx


SSL_CTX = _ssl_context()


def _fetch(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            return json.load(resp)
    except urllib.error.URLError as e:
        if not isinstance(e.reason, ssl.SSLCertVerificationError) or not shutil.which("curl"):
            raise
    cmd = ["curl", "-sSf", "--max-time", str(timeout), "-A", USER_AGENT, url]
    if data is not None:
        cmd[1:1] = ["--data-binary", "@-"]
    out = subprocess.run(cmd, input=data, capture_output=True, check=True)
    return json.loads(out.stdout)


def _with_retries(fn, retries, backoff):
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:  # network blips, throttling (ArcGIS, Overpass 429/504)
            if attempt == retries:
                raise
            print(f"  retry {attempt} after error: {e}", file=sys.stderr)
            time.sleep(backoff * attempt)


def get_json(url, params, retries=3, timeout=60):
    full = f"{url}?{urllib.parse.urlencode(params)}"

    def go():
        body = _fetch(full, timeout=timeout)
        if isinstance(body, dict) and "error" in body:
            raise RuntimeError(body["error"])
        return body

    return _with_retries(go, retries, backoff=2)


def post_json(url, data, retries=3, timeout=180):
    body = urllib.parse.urlencode(data).encode()
    return _with_retries(lambda: _fetch(url, body, timeout), retries, backoff=10)
