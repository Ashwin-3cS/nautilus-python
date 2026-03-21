#!/usr/bin/env python3
"""
Nautilus Python TEE Template — HTTP server for AWS Nitro Enclaves.

Endpoints:
  GET  /health       → {"status": "ok", "template": "python"}
  GET  /attestation  → {"attestation": "<hex-cbor>"}
  POST /sign         → {"signature": "<hex>"}  (body = raw data to sign)

Signing pattern: blake2b256(data) → Ed25519 sign
Compatible with on-chain verify_signed_data<T>.

Bridge: socat TCP-LISTEN:5000 → VSOCK-CONNECT:$CID:5000
"""

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

from src.crypto import KeyPair
from src.nsm import get_attestation, is_enclave

HTTP_PORT = int(os.environ.get("HTTP_PORT", "5000"))

# Generate ephemeral keypair on startup
keypair = KeyPair()


class NautilusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health" or self.path == "/health_check":
            self._json_response({"status": "ok", "template": "python"})

        elif self.path == "/attestation":
            try:
                doc = get_attestation(keypair.public_key)
                self._json_response({"attestation": doc.hex()})
            except Exception as e:
                self._json_response({"error": str(e)}, status=500)

        else:
            self._json_response({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path == "/sign":
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
    print(f"  Mode:       {mode}")
    print(f"  Port:       {HTTP_PORT}")
    print(f"  Public key: {keypair.public_key_hex}")
    print()

    server = HTTPServer(("0.0.0.0", HTTP_PORT), NautilusHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.")
        server.server_close()
