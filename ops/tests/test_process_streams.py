"""Real subprocess-pipe regressions for deadline-bound host framing."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from support.process_streams import iter_process_lines


@contextmanager
def _producer(script, *, text=False):
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


@pytest.mark.parametrize("text", [False, True])
def test_coalesced_utf8_prelude_and_request_arrive_while_producer_waits(text):
    prelude = "준비된 host transport\n"
    request = {"stage_id": "framing-only", "question": "자료 경계"}
    request_line = "MODEL_REQUEST:" + json.dumps(request, ensure_ascii=False) + "\n"
    payload = (prelude + request_line).encode("utf-8")
    script = ("import os;os.write(1," + repr(payload) + ");os.read(0,1)")
    with _producer(script, text=text) as process:
        frames = iter_process_lines(process.stdout, time.monotonic() + 5)
        try:
            assert next(frames) == prelude
            assert next(frames) == request_line
            # The child cannot exit or emit more stdout until this response.
            assert process.poll() is None
            os.write(process.stdin.fileno(), b"x")
            assert next(frames, None) is None
            process.wait(timeout=5)
            assert process.returncode == 0
        finally:
            frames.close()


def test_split_utf8_waits_for_full_line_then_preserves_unterminated_eof():
    character = "지".encode()
    prefix = b"partial:" + character[:1]
    suffix = character[1:] + "\n마지막".encode()
    script = (
        "import os;os.write(1," + repr(prefix) + ");os.write(2,b'ready\\n');"
        "os.read(0,1);os.write(1," + repr(suffix) + ")"
    )
    # Process cleanup precedes executor shutdown, so a failed assertion cannot
    # leave the reader thread waiting on a producer still blocked on stdin.
    with ThreadPoolExecutor(max_workers=1) as pool, _producer(script) as process:
        deadline = time.monotonic() + 5
        frames = iter_process_lines(process.stdout, deadline)
        pending = pool.submit(next, frames)
        ready = iter_process_lines(process.stderr, deadline)
        try:
            assert next(ready) == "ready\n"
        finally:
            ready.close()
        assert not pending.done()
        os.write(process.stdin.fileno(), b"x")
        assert pending.result(timeout=5) == "partial:지\n"
        assert list(frames) == ["마지막"]
        process.wait(timeout=5)
        assert process.returncode == 0


def test_absolute_deadline_expires_while_nonwriting_producer_remains_alive():
    script = "import os;os.write(2,b'ready\\n');os.read(0,1)"
    with _producer(script) as process:
        ready = iter_process_lines(process.stderr, time.monotonic() + 5)
        try:
            assert next(ready) == "ready\n"
        finally:
            ready.close()
        deadline = time.monotonic() + 0.2
        frames = iter_process_lines(process.stdout, deadline)
        with pytest.raises(TimeoutError):
            next(frames)
        assert time.monotonic() >= deadline
        assert process.poll() is None


def test_complete_frames_are_retained_when_producer_has_already_reached_eof():
    expected = ["first\n", "\n", "끝\r\n"]
    payload = "".join(expected).encode("utf-8")
    with _producer("import os;os.write(1," + repr(payload) + ")") as process:
        # The small payload fits in the pipe; make EOF precede any host read.
        process.wait(timeout=5)
        assert process.returncode == 0
        assert list(iter_process_lines(process.stdout, time.monotonic() + 5)) == expected
