"""Bounded local Unix-socket transport for an owner-approved InterOps outbox."""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import stat
import struct
from pathlib import Path
from typing import Any

from .core import AdmissionError, canonical

_MAX_FRAME = 32768


def _credential(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise AdmissionError("INTEROPS_BINDING_INVALID", "credential must be a private regular file")
    value = path.read_bytes()
    if len(value) != 32:
        raise AdmissionError("INTEROPS_BINDING_INVALID", "credential must contain 32 bytes")
    return value


def _read_exact(connection: socket.socket, count: int) -> bytes:
    output = bytearray()
    while len(output) < count:
        chunk = connection.recv(count - len(output))
        if not chunk:
            raise AdmissionError("UNAVAILABLE", "InterOps peer closed an incomplete frame")
        output.extend(chunk)
    return bytes(output)


class UnixInterOpsClient:
    """Only transport bytes; the owner journal decides what may be sent."""

    def __init__(self, socket_path: Path, credential_file: Path, *, timeout: float = 5.0):
        self.socket_path = Path(socket_path)
        self.secret = _credential(Path(credential_file))
        self.timeout = timeout

    def _exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        body = canonical(request)
        if len(body) > _MAX_FRAME:
            raise AdmissionError("INTEROPS_PAYLOAD_LIMIT", "InterOps frame exceeds local bound")
        if self.socket_path.is_symlink():
            raise AdmissionError("INTEROPS_BINDING_INVALID", "socket path is a symlink")
        signature = hmac.new(self.secret, body, hashlib.sha256).digest()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(str(self.socket_path))
                connection.sendall(struct.pack("!I", len(body)) + signature + body)
                length = struct.unpack("!I", _read_exact(connection, 4))[0]
                if length < 2 or length > _MAX_FRAME:
                    raise AdmissionError("UNAVAILABLE", "InterOps response exceeds local bound")
                received_signature = _read_exact(connection, 32)
                received_body = _read_exact(connection, length)
        except (OSError, TimeoutError) as error:
            raise AdmissionError("UNAVAILABLE", "InterOps receiver is unavailable") from error
        expected = hmac.new(self.secret, received_body, hashlib.sha256).digest()
        if not hmac.compare_digest(received_signature, expected):
            raise AdmissionError("UNAUTHENTICATED", "InterOps response authentication failed")
        try:
            response = json.loads(received_body)
        except (UnicodeError, ValueError) as error:
            raise AdmissionError("UNAVAILABLE", "InterOps response is malformed") from error
        if not isinstance(response, dict) or set(response) != {"ok", "result"}:
            raise AdmissionError("UNAVAILABLE", "InterOps response shape is invalid")
        if response["ok"] is not True:
            result = response["result"]
            raise AdmissionError(result.get("code", "UNAVAILABLE"),
                                 result.get("message", "InterOps receiver rejected request"))
        return response["result"]

    def admit(self, envelope: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        return self._exchange({"method": "admit", "envelope": envelope,
                               "candidate": candidate})

    def query_admission(self, *, sender_operation_id: str, idempotency_key: str,
                        request_digest: str) -> dict[str, Any] | None:
        return self._exchange({"method": "query_admission",
                               "sender_operation_id": sender_operation_id,
                               "idempotency_key": idempotency_key,
                               "request_digest": request_digest})
