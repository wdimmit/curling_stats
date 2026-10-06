"""scripts/review.py: the nightly review over a folder of downloaded timelines."""

import importlib.util
import json
import shutil
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "review_script", Path(__file__).resolve().parents[1] / "scripts" / "review.py")
review_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review_script)

FIXTURES = Path(__file__).parent / "fixtures" / "autoreview"


def test_it_lists_the_games_it_would_flag_and_counts_each_check(tmp_path, capsys):
    for p in FIXTURES.glob("*.json"):
        shutil.copy(p, tmp_path / p.name)
    assert review_script.main([str(tmp_path), "--any"]) == 0
    out = capsys.readouterr().out
    assert "s_11DBbXARm30gMQEsy  LFvFYGqdrlk S3" in out
    assert "s_0qsNY1Vdc3vynNPx5  EUpphjp9UMc S5" in out
    assert "s_00hRGMbcChFTNviqg" not in out.split("\n\n")[1]   # clean: not listed
    assert "2/4 games flagged; 2 with any finding" in out
    assert "same_house" in out and "threshold" in out


def test_a_folder_too_small_for_a_baseline_says_so(tmp_path, capsys):
    shutil.copy(FIXTURES / "s_00hRGMbcChFTNviqg.json", tmp_path / "a.json")
    review_script.main([str(tmp_path)])
    assert "fixed floor" in capsys.readouterr().out


def test_a_file_that_is_not_a_timeline_is_skipped(tmp_path, capsys):
    (tmp_path / "games.json").write_text(json.dumps({"games": "not a list"}))
    assert review_script.main([str(tmp_path)]) == 0
    assert "skipped games.json" in capsys.readouterr().err
