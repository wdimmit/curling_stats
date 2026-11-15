"""Turn raw SRT recordings of a sheet's cameras into what the capture writes.

    python scripts/cameras/retime_srt.py SRC_DIR OUT_DIR --sheet 3 --start 1800 --end 4800

``SRC_DIR`` holds what ``srt_recv.py`` left of each of the sheet's cameras
(scratch/camera-direct/srt-s3-1102/): ``<camera>.ts``, the camera's MPEG-TS as
it came, and ``<camera>.pts``, a line a packet: ``pts_s, arrival, wall,
raw_pts``, its arrival on the Ryzen's monotonic clock. ``OUT_DIR`` gets the
four ``<role>.ts`` files ``cameras/recorder.py`` would have made had Start
been pressed ``--start`` seconds after the first packet came: each packet
timed by ``capture.CameraClock`` from its pts and its arrival, less the
camera's delivery delay, each file starting at its camera's first keyframe
after Start, held from zero, and written by the capture's own ``_TsFile``.
``--end`` is where they stop, on the same clock as ``--start``.

One thing differs from a live capture: a camera that went quiet is a gap
here, where the capture's ``Holder`` would have held its last picture.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import av

from curling_score.cameras import capture, inventory

SEATTLE = ZoneInfo("America/Los_Angeles")


def _log(path: Path) -> list[tuple[float, float, float, int]]:
    """A ``.pts`` file's lines: ``(pts_s, arrival, wall, raw_pts)``."""
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            pts_s, arrival, wall, raw = line.split(",")
            rows.append((float(pts_s), float(arrival), float(wall), int(raw)))
    return rows


def retime(src: Path, out: Path, sheet: int, start_s: float, end_s: float) -> dict:
    """Write the sheet's four ``<role>.ts`` into ``out``. ``{role: packets
    written}``, and the wall time of Start under ``"start_wall"``."""
    src, out = Path(src), Path(out)
    cameras = inventory.sheet_cameras(sheet)
    logs = {role: _log(src / f"{camera}.pts") for role, camera in cameras.items()}
    first = min((rows[0] for rows in logs.values()), key=lambda row: row[1])
    t0 = first[1] + start_s                    # Start, on the arrivals' clock
    out.mkdir(parents=True, exist_ok=True)
    written: dict = {"start_wall": first[2] + start_s}
    for role, camera in cameras.items():
        arrivals = {row[3]: row[1] for row in logs[role]}
        clock = capture.CameraClock(t0 + capture.delivery_delay_s(camera))
        unwrap = capture.PtsUnwrap()
        ts = capture._TsFile(out / f"{role}.ts")
        n, started = 0, False
        with av.open(str(src / f"{camera}.ts")) as container:
            for packet in container.demux(video=0):
                if not packet.size or packet.pts is None:
                    continue
                arrival = arrivals.get(packet.pts)
                if arrival is None or arrival < t0:
                    continue
                if not started and not packet.is_keyframe:
                    continue
                started = True
                at = clock.time(unwrap(packet.pts) / 90_000, arrival)
                if at > end_s - start_s:
                    break
                ts.write(bytes(packet), at, packet.is_keyframe)
                n += 1
        written[role] = n
    return written


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("src", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--sheet", type=int, required=True)
    p.add_argument("--start", type=float, required=True,
                   help="Start, in seconds after the first packet came")
    p.add_argument("--end", type=float, required=True, help="where it stops, likewise")
    args = p.parse_args(argv)
    got = retime(args.src, args.out, args.sheet, args.start, args.end)
    wall = datetime.fromtimestamp(got.pop("start_wall"), SEATTLE)
    print(f"Start at {wall:%Y-%m-%d %H:%M:%S %Z}; packets written: "
          + ", ".join(f"{role} {n}" for role, n in got.items()))
    return 0 if all(got.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
