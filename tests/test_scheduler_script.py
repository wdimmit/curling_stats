"""deploy/scheduler.sh against a stand-in gcloud that behaves like the real one
where it matters: `create` fails once a job exists, and `jobs update http`
takes --update-headers, never --headers."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "scheduler.sh"
GCLOUD = """#!/usr/bin/env bash
echo "$*" >> "$GCLOUD_LOG"
[ "$3" = "create" ] && { echo "ALREADY_EXISTS" >&2; exit 1; }
for a in "$@"; do
  [ "$a" = "--headers" ] && { echo "unrecognized arguments: --headers" >&2; exit 2; }
done
exit 0
"""


def run(tmp_path, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gcloud").write_text(GCLOUD)
    (bin_dir / "gcloud").chmod(0o755)
    log = tmp_path / "gcloud.log"
    got = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env={
        **os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "GCLOUD_LOG": str(log),
        "PROJECT_ID": "p", "PUBLIC_BASE_URL": "https://chart.example", "ADMIN_TOKEN": "t",
        **env})
    return got, log.read_text().splitlines() if log.exists() else []


def test_with_both_jobs_in_place_it_updates_both(tmp_path):
    got, calls = run(tmp_path, REVIEW="1")
    assert got.returncode == 0, got.stderr
    updates = [c for c in calls if c.startswith("scheduler jobs update http")]
    assert [c.split()[4] for c in updates] == ["curling-poll-playlists",
                                              "curling-nightly-review"]
    assert all("--update-headers Authorization=Bearer t" in c for c in updates)


def test_without_review_it_leaves_the_review_job_alone(tmp_path):
    got, calls = run(tmp_path)
    assert got.returncode == 0, got.stderr
    assert not any("curling-nightly-review" in c for c in calls)
