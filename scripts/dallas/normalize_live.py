"""Normalise DFW's live stream as it is recorded: `normalize.py`, frame by frame, in real time.

Follows the growing raw recording (yt-dlp's MPEG-TS, from the stream's first
segment) with ``ffmpeg -follow 1``, maps every score-view frame into the
three-panel layout as `normalize.py` does, and writes a growing MPEG-TS the
live lane reads as its recording. Same frame count and rate as the input, so
its clock is the stream's.

A dissolve is only known once it is over, so each frame is held until GUARD
frames after it have been classified: the output runs GUARD frames behind.

Beside the output, ``norm.head`` says how many seconds have been written (the
live lane's `head_s`), rewritten every second; ``norm.done`` appears when the
input has ended -- nothing new for TIMEOUT_S -- and everything is flushed.

    python normalize_live.py PLATES RAW.ts OUT_DIR [--green-to-red] [--layout]
"""

import argparse
import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize as N  # noqa: E402

TIMEOUT_S = 300  # no new frame for this long: the stream is over


def _reader(raw: Path, timeout_s: float, size=(N.W, N.H)):
    return subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-follow", "1", "-rw_timeout", str(int(timeout_s * 1_000_000)), "-i", str(raw),
        "-map", "0:v:0", "-fps_mode", "passthrough", "-vf", f"scale={size[0]}:{size[1]}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)


def _writer(path: Path, fps: float):
    return subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{N.W}x{N.H}", "-r", f"{fps}", "-i", "-",
        "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "21", "-b:v", "0",
        "-g", "60", "-pix_fmt", "yuv420p", "-f", "mpegts", str(path)], stdin=subprocess.PIPE)


def _say(path: Path, text: str):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plates"); ap.add_argument("raw", type=Path); ap.add_argument("out", type=Path)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S, help="seconds without a new frame = the end")
    ap.add_argument("--green-to-red", action="store_true", help="the scaffold, not the fix")
    ap.add_argument("--layout", action="store_true",
                    help="PLATES holds T.png + layout.json (normalize_layout.py): another event's framing")
    a = ap.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    while not a.raw.exists():
        time.sleep(2)
    if a.layout:
        from normalize_layout import LayoutPlates
        plates = LayoutPlates(a.plates)
    else:
        plates = N.Plates(a.plates)
    w, h = getattr(plates, "size", (N.W, N.H))
    src, enc = _reader(a.raw, a.timeout, (w, h)), _writer(a.out / "norm.ts", a.fps)
    size = w * h * 3
    pending = deque()          # (frame, kind) not yet written
    kinds = deque(maxlen=2 * N.GUARD + 1)
    last = plates.T.copy()
    written, said_at, t0 = 0, 0.0, time.monotonic()
    counts = {}

    def emit(frame, kind, bad):
        nonlocal last, written
        if not bad:
            if a.layout:
                last = plates.convert(frame, kind)
            else:
                last = frame if kind == "T" else plates.to_three_panel(frame, int(kind[1]))
        out = N.green_to_red(last) if a.green_to_red else last
        enc.stdin.write(out.tobytes())
        written += 1

    def guarded(i_from_end):
        """Whether the frame i_from_end back from the newest is in or beside a dissolve."""
        ks = list(kinds)
        j = len(ks) - 1 - i_from_end
        window = ks[max(0, j - N.GUARD):j + N.GUARD + 1]
        return ks[j] == "?" or len(set(window)) > 1

    while True:
        buf = src.stdout.read(size)
        if len(buf) < size:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        kind, _, _ = plates.classify(frame)
        counts[kind] = counts.get(kind, 0) + 1
        kinds.append(kind)
        pending.append((frame, kind))
        if len(pending) > N.GUARD:
            f, k = pending.popleft()
            emit(f, k, guarded(N.GUARD))
        now = time.monotonic()
        if now - said_at >= 1.0:
            _say(a.out / "norm.head", f"{written / a.fps:.3f}\n")
            said_at = now
            if written % (int(a.fps) * 60) < int(a.fps):
                print(f"{written / a.fps:8.1f} s written, {written / (now - t0):.1f} fps, {counts}",
                      flush=True)
    n = len(pending)
    while pending:
        n -= 1
        f, k = pending.popleft()
        emit(f, k, guarded(n) if n < len(kinds) else True)
    enc.stdin.close()
    enc.wait()
    _say(a.out / "norm.head", f"{written / a.fps:.3f}\n")
    _say(a.out / "norm.done", f"{written} frames\n")
    print(f"done: {written} frames, {counts}", flush=True)


if __name__ == "__main__":
    main()
