#!/usr/bin/env python3
"""
Nautilus Python TEE Template — HTTP server for AWS Nitro Enclaves.

Endpoints:
  GET  /health       → {"status": "ok", "template": "python"}
  GET  /attestation  → {"attestation": "<hex-cbor>"}
  POST /sign         → {"signature": "<hex>"}  (body = raw data to sign)
  GET  /logs         → {"lines": [...], "count": N}  (recent log lines)

Signing pattern: blake2b256(data) → Ed25519 sign
Compatible with on-chain verify_signed_data<T>.

Bridge: socat TCP-LISTEN:5000 → VSOCK-CONNECT:$CID:5000
"""

import json
import os
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from src.crypto import KeyPair
from src.nsm import get_attestation, is_enclave

HTTP_PORT = int(os.environ.get("HTTP_PORT", "5000"))

# Generate ephemeral keypair on startup
keypair = KeyPair()


# ── In-memory log ring buffer ────────────────────────────────────────

class LogBuffer:
    def __init__(self, capacity=1000):
        self._lines = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def push(self, line):
        with self._lock:
            self._lines.append(line)

    def recent(self, n):
        with self._lock:
            items = list(self._lines)
        return items[-min(n, len(items)):]


log_buffer = LogBuffer(1000)


def log(msg):
    """Log a message to both stdout and the ring buffer."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} INFO  {msg}"
    log_buffer.push(line)
    print(f"  {msg}")


class NautilusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health" or path == "/health_check":
            log(f"GET {path}")
            self._json_response({"status": "ok", "template": "python"})

        elif path == "/attestation":
            log("GET /attestation")
            try:
                doc = get_attestation(keypair.public_key)
                self._json_response({"attestation": doc.hex()})
            except Exception as e:
                self._json_response({"error": str(e)}, status=500)

        elif path == "/logs":
            qs = parse_qs(parsed.query)
            n = min(int(qs.get("lines", ["100"])[0]), 1000)
            lines = log_buffer.recent(n)
            self._json_response({"lines": lines, "count": len(lines)})

        else:
            self._json_response({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path == "/sign":
            log("POST /sign")
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b""
            try:
                signature = keypair.sign(body)
                self._json_response({"signature": signature.hex()})
            except Exception as e:
                self._json_response({"error": str(e)}, status=500)
        else:
            self._json_response({"error": "not found"}, status=404)

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  {args[0]}")


if __name__ == "__main__":
    mode = "enclave" if is_enclave() else "development"
    print()
    print("  Nautilus Python TEE App")
    print("  " + "=" * 40)
    log(f"Mode: {mode}")
    log(f"Port: {HTTP_PORT}")
    log(f"Public key: {keypair.public_key_hex}")
    print()

    server = HTTPServer(("0.0.0.0", HTTP_PORT), NautilusHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.")
        server.server_close()
