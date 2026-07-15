"""Generate the deterministic, non-speech STT runtime smoke fixture."""

from __future__ import annotations

import argparse
import io
import os
import struct
import uuid
import wave
from collections.abc import Sequence
from pathlib import Path


SAMPLE_RATE = 16_000
DURATION_SECONDS = 2
FRAME_COUNT = SAMPLE_RATE * DURATION_SECONDS


def fixture_wave_bytes() -> bytes:
    """Return a stable mono PCM WAV containing a bounded synthetic test signal."""

    # Integer-only construction keeps the bytes identical across Python/CPU builds.
    samples = [
        (((frame * 97) % 2_048) - 1_024) * 12
        for frame in range(FRAME_COUNT)
    ]
    pcm = struct.pack(f"<{FRAME_COUNT}h", *samples)
    output = io.BytesIO()
    with wave.open(output, "wb") as fixture:
        fixture.setnchannels(1)
        fixture.setsampwidth(2)
        fixture.setframerate(SAMPLE_RATE)
        fixture.writeframes(pcm)
    return output.getvalue()


def write_fixture(path: Path) -> None:
    """Atomically replace *path* with the canonical generated fixture."""

    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(fixture_wave_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    write_fixture(args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a build helper
    raise SystemExit(main())
