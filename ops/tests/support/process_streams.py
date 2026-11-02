"""Deadline-bound UTF-8 framing for host smoke subprocess output."""
from __future__ import annotations

import os
import selectors
import time


def iter_process_lines(stream, deadline):
    """Yield UTF-8 lines from an unread subprocess pipe until EOF.

    Read the descriptor directly, never its buffered/text wrapper. Keep line
    endings and final unterminated data, and leave stream ownership to the caller.
    ``deadline`` is an absolute ``time.monotonic()`` value shared by all reads.
    """
    descriptor = stream.fileno()
    pending = bytearray()
    eof = False
    with selectors.DefaultSelector() as selector:
        selector.register(descriptor, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Process output deadline exceeded")
            newline = pending.find(b"\n")
            if newline >= 0:
                frame = bytes(pending[:newline + 1])
                del pending[:newline + 1]
                yield frame.decode("utf-8")
            elif eof:
                if pending:
                    yield bytes(pending).decode("utf-8")
                return
            else:
                if not selector.select(timeout=remaining):
                    raise TimeoutError("Process output deadline exceeded")
                chunk = os.read(descriptor, 65536)
                if chunk:
                    pending.extend(chunk)
                else:
                    eof = True
