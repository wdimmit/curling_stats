"""The phase 2 gate's tools: retiming raw SRT recordings as the capture would,
composing camera files offline, and comparing two practice replays."""

import importlib.util
import io
import json
import sys
from fractions import Fraction
from pathlib import Path

import av
import pytest

from curling_score.cameras import capture, inventory, layout

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "cameras"


def h264(count, gop=30, size=(64, 48)):
    """H.264 as the cameras send it (SPS and PPS before every keyframe, no
    B-frames), any length: [(packet, keyframe)]."""
    import numpy as np

    codec = av.CodecContext.create("libx264", "w")
    (codec.width, codec.height), codec.pix_fmt = size, "yuv420p"
    codec.time_base = Fraction(1, 60)
    codec.thread_count = 1
    codec.options = {"preset": "ultrafast", "x264-params":
                     f"repeat-headers=1:keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0"}
    out = []
    for i in range(count):
        frame = av.VideoFrame.from_ndarray(
            np.full((size[1], size[0], 3), 8 * (i % 32), np.uint8), format="rgb24")
        frame.pts = i
        out += [(bytes(p), p.is_keyframe) for p in codec.encode(frame)]
    return out + [(bytes(p), p.is_keyframe) for p in codec.encode(None)]


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_script", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- retime_srt --------------------------------------------------------------------

BASE_PTS = 126_000        # srt_recv's files start ~1.4 s in, as ffmpeg rebases them
ARRIVED = 871_041.682     # the Ryzen's monotonic clock as the first packet came
DELIVERY = 0.6            # every packet reached the Ryzen this long after its pts said


def _srt_recording(directory, camera, packets):
    """A raw camera recording as srt_recv.py left it: ``<camera>.ts`` with the
    camera's packets 1/60 s apart, and ``<camera>.pts``, a line a packet:
    pts_s, arrival (monotonic), wall, raw pts."""
    buf = io.BytesIO()
    with av.open(io.BytesIO(packets[0][0]), format="h264") as raw, \
            av.open(buf, "w", format="mpegts", options={"mpegts_copyts": "1"}) as out:
        stream = out.add_stream_from_template(raw.streams.video[0])
        stream.time_base = Fraction(1, 90_000)
        for i, (data, key) in enumerate(packets):
            packet = av.Packet(data)
            packet.stream, packet.time_base = stream, Fraction(1, 90_000)
            packet.pts = packet.dts = BASE_PTS + i * 1500
            packet.is_keyframe = key
            out.mux(packet)
    (directory / f"{camera}.ts").write_bytes(buf.getvalue())
    lines = []
    for i in range(len(packets)):
        raw_pts = BASE_PTS + i * 1500
        arrival = ARRIVED + DELIVERY + i / 60
        lines.append(f"{raw_pts / 90_000:.6f},{arrival:.6f},{1_793_676_092.0 + i / 60:.6f},{raw_pts}")
    (directory / f"{camera}.pts").write_text("\n".join(lines) + "\n")


@pytest.fixture
def raw_sheet(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    packets = h264(120, gop=30)
    for camera in inventory.sheet_cameras(2).values():
        _srt_recording(src, camera, packets)
    return src


def read_ts(path):
    with av.open(str(path)) as c:
        return [(float(p.pts * p.time_base), p.is_keyframe) for p in c.demux(video=0) if p.size]


def test_retime_times_packets_as_the_capture_would(raw_sheet, tmp_path):
    retime = _load("retime_srt")
    out = tmp_path / "out"
    retime.retime(raw_sheet, out, 2, 0.0, 10.0)
    delay = capture.delivery_delay_s(inventory.sheet_cameras(2)["house_a"])
    got = read_ts(out / "house_a.ts")
    assert got[0] == (0.0, True)                   # the first keyframe, held from zero
    # Packet k reached the Ryzen DELIVERY + k/60 after the first's pts, and is
    # put the camera's delivery delay before its arrival, on the recording's
    # clock (t0 = the earliest arrival).
    for k in (40, 80, 119):
        assert got[k][0] == pytest.approx(DELIVERY + k / 60 - DELIVERY - delay, abs=1e-3)
    for role in ("house_h", "long_a", "long_h"):
        assert (out / f"{role}.ts").exists()


def test_retime_starts_at_the_first_keyframe_after_start(raw_sheet, tmp_path):
    retime = _load("retime_srt")
    out = tmp_path / "out"
    retime.retime(raw_sheet, out, 2, 0.4, 10.0)
    got = read_ts(out / "house_a.ts")
    # Keyframes every 30 packets: the first at or after 0.4 s is packet 30,
    # and nothing before it is written.
    assert got[0] == (0.0, True)
    assert len(got) == 120 - 30


# --- compose_offline -------------------------------------------------------------------

def test_compose_command_reads_the_four_files_with_the_layouts_graphs(tmp_path):
    compose = _load("compose_offline")
    cmd = compose.command(tmp_path, tmp_path / "composite.ts", 3, seconds=60.0)
    sheet = layout.load(3)
    inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
    assert inputs == [str(tmp_path / f"{p.role}.ts") for p in sheet.placements]
    roles = {p.role: i for i, p in enumerate(sheet.placements)}
    for graph in layout.filtergraphs(sheet, inputs=roles):
        assert graph in cmd
    assert "-copyts" in cmd and "h264_nvenc" in cmd
    assert cmd[cmd.index("-t") + 1] == "60.000"
    assert cmd[-1] == str(tmp_path / "composite.ts")


# --- compare_replays ----------------------------------------------------------------------

def throw(t, *, house="top", color="red", x=0.0, y=0.0, arrived=True, split=1.0, latency=8.0):
    return {"id": f"t_{t}", "house": house, "color": color, "arrived": arrived,
            "t_rest_s": t if arrived else None, "t_release_s": t - 20.0,
            "rest": {"x": x, "y": y} if arrived else None, "split_s": split,
            "latency_s": latency}


def write(directory, throws):
    directory.mkdir()
    (directory / "throws.jsonl").write_text("".join(json.dumps(t) + "\n" for t in throws))
    return directory


def test_compare_pairs_and_reports(tmp_path, capsys):
    compare = _load("compare_replays")
    a = write(tmp_path / "comp", [throw(100.0), throw(160.0, color="yellow"), throw(220.0)])
    b = write(tmp_path / "cams", [throw(100.4, x=0.03), throw(160.2, color="yellow")])
    assert compare.main([str(a), str(b)]) == 1
    out = capsys.readouterr().out
    assert "only in comp: t_220.0" in out
    assert "3.0 cm" in out


def test_compare_passes_a_matching_pair(tmp_path, capsys):
    compare = _load("compare_replays")
    a = write(tmp_path / "comp", [throw(100.0), throw(160.0, color="yellow")])
    b = write(tmp_path / "cams", [throw(100.3, x=0.01), throw(160.2, color="yellow", y=0.015)])
    assert compare.main([str(a), str(b)]) == 0
    assert "gate: PASS" in capsys.readouterr().out


def test_compare_pairs_one_release_whose_rests_were_seen_apart():
    """11/04 gate: one throw, released 0.02 s apart in the two runs, last seen
    1.8 s apart as it left the panel. It is one throw, not two unmatched."""
    compare = _load("compare_replays")
    x = {**throw(2511.85), "t_release_s": 2485.93}
    y = {**throw(2513.66), "t_release_s": 2485.94}
    pairs, only_a, only_b = compare.pair([x], [y])
    assert (len(pairs), only_a, only_b) == (1, [], [])


def test_compare_fails_on_latency(tmp_path, capsys):
    compare = _load("compare_replays")
    a = write(tmp_path / "comp", [throw(100.0 + 60 * i) for i in range(10)])
    b = write(tmp_path / "cams", [throw(100.0 + 60 * i, latency=25.0 if i < 2 else 9.0)
                                  for i in range(10)])
    assert compare.main([str(a), str(b)]) == 1
    assert "latency p90" in capsys.readouterr().out
