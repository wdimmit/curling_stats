"""Compose a sheet's camera files into YouTube's picture, as recording did.

    python scripts/cameras/compose_offline.py DIR composite.ts --sheet 3

``DIR`` holds the sheet's four ``<role>.ts`` (``cameras/recorder.py``, or
``retime_srt.py``). This runs the composer the recorder ran until camera-direct
phase 2 -- the layout's graphs (``layout.filtergraphs``), ``-copyts``, NVENC
at the same rate -- over the files in place of its FIFOs, so one recording can
be read both ways: per view, and as the composite the tracker was built on.
The composite's background never ends by itself, so it stops at the shortest
file's end.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from curling_score.cameras import layout
from curling_score.ingest import frames

# As the composer had them (recorder.py before phase 2): two threads for each
# camera's decoder and for each graph's slices kept up at 2.6 cores a sheet.
DECODE_THREADS = 2
FILTER_THREADS = 2


def command(directory: Path, out: Path, sheet: int, *, seconds: float) -> list[str]:
    """The composer's command over ``directory``'s four files."""
    sheet_layout = layout.load(sheet)
    inputs = {p.role: i for i, p in enumerate(sheet_layout.placements)}
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-stats", "-y",
           "-filter_complex_threads", str(FILTER_THREADS), "-copyts"]
    for p in sheet_layout.placements:
        cmd += ["-threads", str(DECODE_THREADS), "-i", str(Path(directory) / f"{p.role}.ts")]
    for graph in layout.filtergraphs(sheet_layout, inputs=inputs):
        cmd += ["-filter_complex", graph]
    cmd += ["-map", "[out]",
            "-c:v", "h264_nvenc", "-b:v", "20M", "-maxrate", "25M", "-g", "60", "-bf", "0",
            "-t", f"{seconds:.3f}", "-f", "mpegts", str(out)]
    return cmd


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("dir", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--sheet", type=int, required=True)
    args = p.parse_args(argv)
    seconds = min(frames.probe(args.dir / f"{pl.role}.ts").duration_s
                  for pl in layout.load(args.sheet).placements)
    return subprocess.run(command(args.dir, args.out, args.sheet, seconds=seconds)).returncode


if __name__ == "__main__":
    sys.exit(main())
