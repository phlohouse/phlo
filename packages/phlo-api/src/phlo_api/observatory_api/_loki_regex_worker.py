"""Standalone regex filter worker for the Loki API.

Run as ``python -I _loki_regex_worker.py``: a clean interpreter with no project
imports, so it boots fast and shares no state with the multi-threaded API
process. The API spawns one worker and reuses it across requests; on timeout it
is killed and respawned, so a stuck evaluation can never linger.

Protocol over stdin/stdout: length-prefixed pickle frames matching
``multiprocessing.connection`` (a big-endian ``!i`` length header), so the
parent can use ``Connection`` objects for framing and ``poll`` timeouts.

    request:  ``(pattern_text, messages)``
    response: ``("matches", [indexes])`` | ``("invalid", [])`` | ``("error", [])``

Only matching indexes and status strings cross the pipe, so failures can never
echo log contents back to the API process.
"""

from __future__ import annotations

import pickle
import re
import struct
import sys

_FRAME_HEADER = struct.Struct("!i")


def _read_frame(stream) -> tuple[str, list[str]] | None:
    header = stream.read(_FRAME_HEADER.size)
    if len(header) != _FRAME_HEADER.size:
        return None
    (size,) = _FRAME_HEADER.unpack(header)
    payload = stream.read(size)
    if len(payload) != size:
        return None
    return pickle.loads(payload)


def _write_frame(stream, payload: object) -> None:
    encoded = pickle.dumps(payload)
    stream.write(_FRAME_HEADER.pack(len(encoded)) + encoded)
    stream.flush()


def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        job = _read_frame(stdin)
        if job is None:
            return
        pattern_text, messages = job
        try:
            pattern = re.compile(pattern_text)
        except Exception:
            _write_frame(stdout, ("invalid", []))
            continue
        try:
            matches = [index for index, message in enumerate(messages) if pattern.search(message)]
        except Exception:
            _write_frame(stdout, ("error", []))
            continue
        _write_frame(stdout, ("matches", matches))


if __name__ == "__main__":
    main()
