"""analyze with the left long camera replaced by the logo-tied fit (tie.json)."""
import json
import sys

from curling_score import analyze, cli
from curling_score.geometry.sideview import SideView

tie = json.load(open("/w/tie.json"))["hog_fixed"]
orig = analyze._end_views


def patched(*a, **k):
    views = orig(*a, **k)
    if views and "left" in views:
        v = views["left"]
        views["left"] = SideView(rect=v.rect, tee_row=tie["tee_row"], hog_row=tie["hog_row"], d_m=tie["d_m"],
                                 centre_col=tie["centre_col"], lat_px_per_m_at_tee=tie["lat_px_per_m_at_tee"],
                                 centre_line=tuple(tie["centre_line"]), end_camera=False)
        print(f"left long camera tied to the logo: d {tie['d_m']:.2f} m", flush=True)
    return views


analyze._end_views = patched
sys.argv = ["curling-score"] + sys.argv[1:]
sys.exit(cli.main())
