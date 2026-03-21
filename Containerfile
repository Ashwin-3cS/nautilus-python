FROM python:3.12-slim

WORKDIR /app

# Install socat for VSOCK → TCP bridging inside the enclave
RUN apt-get update && apt-get install -y --no-install-recommends socat && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ src/
COPY app.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 5000

CMD ["./entrypoint.sh"]
