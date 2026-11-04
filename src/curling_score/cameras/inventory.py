"""The club's 20 cameras: which is which, and where to reach them.

Each sheet has a house camera (``HOUSE``, a fisheye overhead) and a long view
at each end, A and H. The long views are ``POV`` cameras on sheets 1 and 5 and
``PTZ`` cameras on sheets 2-4. Names and addresses are as NDI's finder listed
them on 2026-10-24 (``1A-POV (HX-Stream-192.168.0.231)`` and so on).

Every camera serves SRT on UDP 1600 at its address (``capture.srt_url``), to
any caller, with no stream id or passphrase. A second caller to a camera gets
its own stream and leaves the first alone (measured 2026-11-03), so a script
or ``cameras-calibrate`` may pull a camera a practice session is recording.
Keep to two of ours per house camera all the same: over NDI a house camera
took only about three receivers, the club's production among them.
"""

from __future__ import annotations

CAMERAS: dict[str, str] = {
    "1A-POV": "192.168.0.231",
    "2A-PTZ": "192.168.0.232",
    "3A-PTZ": "192.168.0.233",
    "4A-PTZ": "192.168.0.234",
    "5A-POV": "192.168.0.235",
    "1H-POV": "192.168.0.236",
    "2H-PTZ": "192.168.0.237",
    "3H-PTZ": "192.168.0.238",
    "4H-PTZ": "192.168.0.239",
    "5H-POV": "192.168.0.240",
    "1A-HOUSE": "192.168.0.241",
    "2A-HOUSE": "192.168.0.242",
    "3A-HOUSE": "192.168.0.243",
    "4A-HOUSE": "192.168.0.244",
    "5A-HOUSE": "192.168.0.245",
    "1H-HOUSE": "192.168.0.246",
    "2H-HOUSE": "192.168.0.247",
    "3H-HOUSE": "192.168.0.248",
    "4H-HOUSE": "192.168.0.249",
    "5H-HOUSE": "192.168.0.250",
}


def sheet_cameras(sheet: int) -> dict[str, str]:
    """Role (``house_a``, ``house_h``, ``long_a``, ``long_h``) -> camera name."""
    if not 1 <= sheet <= 5:
        raise ValueError(f"no sheet {sheet}")
    long = "POV" if sheet in (1, 5) else "PTZ"
    return {
        "house_a": f"{sheet}A-HOUSE",
        "house_h": f"{sheet}H-HOUSE",
        "long_a": f"{sheet}A-{long}",
        "long_h": f"{sheet}H-{long}",
    }

