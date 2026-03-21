"""
NSM (Nitro Security Module) interface for AWS Nitro Enclaves.

Communicates with /dev/nsm via ioctl to request attestation documents.
Falls back to a mock when running outside an enclave (development mode).
"""

import ctypes
import ctypes.util
import os
import struct

# NSM ioctl request type
NSM_IOCTL_REQUEST = 0xC008_0E00  # _IOWR(0x0E, 0, 8)
NSM_DEV_PATH = "/dev/nsm"


def _cbor_encode_map(d: dict) -> bytes:
    """Minimal CBOR encoder for a flat dict with string keys and bytes/None values."""
    import cbor2
    return cbor2.dumps(d)


def _cbor_decode(data: bytes):
    """Decode CBOR bytes."""
    import cbor2
    return cbor2.loads(data)


def is_enclave() -> bool:
    """Check if running inside a Nitro Enclave."""
    return os.path.exists(NSM_DEV_PATH)


def get_attestation(public_key: bytes, nonce: bytes = b"") -> bytes:
    """
    Request an attestation document from the NSM.

    Args:
        public_key: Ed25519 public key to embed in the attestation document.
        nonce: Optional nonce bytes.

    Returns:
        Raw CBOR-encoded COSE_Sign1 attestation document.
    """
    if not is_enclave():
        return _mock_attestation(public_key)

    import cbor2

    # Build the NSM request
    request = {
        "Attestation": {
            "public_key": public_key,
            "user_data": None,
            "nonce": nonce if nonce else None,
        }
    }
    req_bytes = cbor2.dumps(request)

    # Open NSM device
    fd = os.open(NSM_DEV_PATH, os.O_RDWR)
    try:
        # Allocate request/response buffers
        req_buf = ctypes.create_string_buffer(req_bytes)
        resp_buf = ctypes.create_string_buffer(16384)  # 16KB response buffer

        # Pack the ioctl struct: request_ptr(u32) + request_len(u32) + response_ptr(u32) + response_len(u32)
        # On 64-bit, we need pointer-sized fields
        class NsmMessage(ctypes.Structure):
            _fields_ = [
                ("request", ctypes.c_char_p),
                ("request_len", ctypes.c_uint32),
                ("response", ctypes.c_char_p),
                ("response_len", ctypes.c_uint32),
            ]

        msg = NsmMessage()
        msg.request = ctypes.cast(req_buf, ctypes.c_char_p)
        msg.request_len = len(req_bytes)
        msg.response = ctypes.cast(resp_buf, ctypes.c_char_p)
        msg.response_len = 16384

        # Load libc for ioctl
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        ret = libc.ioctl(fd, NSM_IOCTL_REQUEST, ctypes.byref(msg))
        if ret != 0:
            errno = ctypes.get_errno()
            raise OSError(f"NSM ioctl failed with errno {errno}")

        # Extract response
        resp_data = resp_buf.raw[:msg.response_len]
        resp = cbor2.loads(resp_data)

        if "Attestation" in resp:
            return resp["Attestation"]["document"]
        elif "Error" in resp:
            raise RuntimeError(f"NSM error: {resp['Error']}")
        else:
            raise RuntimeError(f"Unexpected NSM response: {resp}")

    finally:
        os.close(fd)


def _mock_attestation(public_key: bytes) -> bytes:
    """
    Generate a mock attestation document for development/testing.
    Returns a minimal CBOR structure that the CLI can parse.
    """
    import cbor2

    mock_pcrs = {
        0: b"\x00" * 48,
        1: b"\x00" * 48,
        2: b"\x00" * 48,
    }

    # Build a mock attestation payload (CBOR map matching NSM format)
    payload = cbor2.dumps({
        "pcrs": mock_pcrs,
        "public_key": public_key,
        "module_id": "mock-enclave",
        "timestamp": 0,
        "digest": "SHA384",
    })

    # Wrap in COSE_Sign1 structure: [protected, unprotected, payload, signature]
    # Tag 18 = COSE_Sign1
    cose = cbor2.CBORTag(18, [
        b"",          # protected headers (empty)
        {},           # unprotected headers
        payload,      # the attestation payload
        b"\x00" * 64  # mock signature
    ])

    return cbor2.dumps(cose)
