"""scripts/line/validate_lines.py's summary on schema-10 timelines."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/line/validate_lines.py"
spec = importlib.util.spec_from_file_location("validate_lines", SCRIPT)
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


def doc(*lines):
    shots = [{"number": i + 1, "target_broom": {"x": 1.0, "y": 0.0}, "missing": False, "line": l}
             for i, l in enumerate(lines)]
    return {"games": [{"index": 1, "ends": [{"number": 1, "shots": shots}]}]}


def test_a_schema_10_timeline_is_summarised(capsys):
    measured = {"at_broom": {"x": 1.1, "miss_m": 0.1}}
    refused = {"at_broom": {"x": None, "miss_m": None}, "final_refused": True}
    V.summary(doc(measured, refused))
    out = capsys.readouterr().out
    assert "with a line 2" in out
    assert "over 1 shots" in out
    assert "final refused 1" in out
    assert "confirmed" not in out
