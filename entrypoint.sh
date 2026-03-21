#!/bin/bash
# Entrypoint for the Python enclave container.
# Runs socat to bridge VSOCK:5000 → TCP:localhost:5000,
# then starts the Python app on localhost:5000.

set -e

# Start socat: listen on VSOCK port 5000, forward to Python app on localhost:5000
socat VSOCK-LISTEN:5000,fork,reuseaddr TCP-CONNECT:127.0.0.1:5000 &
SOCAT_PID=$!

echo "socat bridge started (VSOCK:5000 → TCP:127.0.0.1:5000, PID=$SOCAT_PID)"

# Start the Python app
exec python3 app.py
