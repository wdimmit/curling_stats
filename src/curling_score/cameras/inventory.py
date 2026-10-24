"""The club's 20 NDI cameras: which is which, and how libndi finds them.

Each sheet has a house camera (``HOUSE``, a fisheye overhead) and a long view
at each end, A and H. The long views are ``POV`` cameras on sheets 1 and 5 and
``PTZ`` cameras on sheets 2-4. Names and addresses are as the finder listed
them on 2026-10-24 (``1A-POV (HX-Stream-192.168.0.231)`` and so on).

libndi finds sources with mDNS, and the Ryzen runs no avahi-daemon, so it is
given the cameras' addresses instead: ``ndi-config.v1.json`` in the directory
``NDI_CONFIG_DIR`` names.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

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

CONFIG_NAME = "ndi-config.v1.json"


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


def ndi_config(path: Path) -> None:
    """Write ``ndi-config.v1.json`` listing every camera into the directory
    ``path``, for ``NDI_CONFIG_DIR``.

    A sheet's captures share one directory and start together, so the file is
    replaced whole, never seen half written.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    # No discovery server: the finder asks each address itself.
    config = {"ndi": {"networks": {"ips": ",".join(CAMERAS.values()), "discovery": ""}}}
    part = path / f".{CONFIG_NAME}.{os.getpid()}"
    part.write_text(json.dumps(config))
    os.replace(part, path / CONFIG_NAME)
