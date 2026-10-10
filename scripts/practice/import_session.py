"""A replayed stretch of a practice stream, made someone's practice session.

`curling-score practice-replay` writes the throws a worker would have published
for a stretch of a stream's video. This puts them where the site reads them: the
document as a finished watch's practice.json, then a practice run, job and
watch, ended, and last a session over the stretch for the person, ended too --
so it shows up in their /practice list only once everything it reads is there.

The replay's clock is the video's, so a throw at t seconds was released at
stream start + t: ``--stream-start`` is when the stream went live (yt-dlp's
release_timestamp; the practice run's published_at).

    AT=$(gcloud auth print-access-token) python scripts/practice/import_session.py \\
        out/practice.json --video qJhDHMMtHsE --sheet 3 --from 959 --to 3231 \\
        --stream-start 1791648022 --user <uid> --hand left

Needs the person's own gcloud login in $AT: there is no admin endpoint for this.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from curling_score.service import slug
from curling_score.service.records import Job, PracticeSession, PracticeWatch, Run
from curling_score.service.store import practice_key

PROJECT = "curling-stats-508323"
BUCKET = "curling-chart-curling-stats-508323"


def upload_clips(doc, clips_dir, video_id, run_id) -> int:
    """Upload the clips the document says it has; a throw whose file is
    missing is marked as having none. How many went up."""
    files = []
    for th in doc["throws"]:
        f = os.path.join(clips_dir, f"{th['id']}.mp4") if clips_dir else None
        if th.get("clip") and f and os.path.isfile(f):
            files.append(f)
        else:
            th["clip"] = False
    if files:
        subprocess.run(["gcloud", "storage", "cp", "--content-type=video/mp4", *files,
                        f"gs://{BUCKET}/runs/{video_id}/{run_id}/clips/"], check=True)
    return len(files)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("doc", help="practice.json from practice-replay")
    ap.add_argument("--video", required=True)
    ap.add_argument("--sheet", type=int, required=True)
    ap.add_argument("--from", dest="from_s", type=float,
                    help="where the session starts, in seconds into the video")
    ap.add_argument("--to", dest="to_s", type=float,
                    help="where it ends, in seconds into the video")
    ap.add_argument("--stream-start", type=float, required=True,
                    help="when the stream went live, as a Unix time")
    ap.add_argument("--user", help="the person's user id")
    ap.add_argument("--hand", choices=("left", "right"), default="right")
    ap.add_argument("--like-run", help="a practice run on the same video to take its "
                                       "title, channel and processing version from")
    ap.add_argument("--replace-run", help="put the document (and clips) in place of this "
                                          "practice run's, keeping its run, watch and sessions")
    ap.add_argument("--clips", help="a directory of <throw id>.mp4 clips to upload with it")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not args.replace_run and None in (args.from_s, args.to_s, args.user):
        ap.error("--from, --to and --user are needed for a new session")

    from google.cloud import firestore
    from google.oauth2.credentials import Credentials

    from curling_score.service.firestore_repo import FirestoreRepo

    repo = FirestoreRepo(client=firestore.Client(project=PROJECT,
                                                 credentials=Credentials(os.environ["AT"])))
    like = repo.get_run(args.like_run) if args.like_run else None
    now = datetime.now(timezone.utc)

    def wall(t):
        return datetime.fromtimestamp(args.stream_start + t, tz=timezone.utc)

    with open(args.doc) as f:
        doc = json.load(f)
    doc["status"] = "ended"
    doc["wall_t0"] = args.stream_start + (doc.get("t0_s") or 0.0)

    if args.replace_run:
        run = repo.get_run(args.replace_run)
        watch = next(w for w in repo.watches_for_sheet(run.sheet) if w.run_id == run.id)
        ids = {th["id"] for th in doc["throws"]}
        for s in repo.sessions_for_watch(watch.id):
            gone = [h for h in s.hidden if h not in ids]
            print(f"session {s.id}: {len(s.hidden)} hidden, {len(gone)} no longer match")
        print(f"run {run.id}: {len(doc['throws'])} throw(s), "
              f"{sum(1 for th in doc['throws'] if th.get('clip'))} with a clip")
        if args.dry_run:
            return 0
        sent = upload_clips(doc, args.clips, args.video, run.id)
        path = os.path.join(os.path.dirname(os.path.abspath(args.doc)), "practice.import.json")
        with open(path, "w") as f:
            json.dump(doc, f)
        subprocess.run(["gcloud", "storage", "cp", "--content-type=application/json", path,
                        f"gs://{BUCKET}/{practice_key(args.video, run.id)}"], check=True)
        repo.update_watch(watch.id, revised_at=now, throws=len(doc["throws"]))
        print(f"{sent} clip(s) uploaded; practice.json replaced")
        return 0

    run = Run(id=slug.new_run_id(), video_id=args.video,
              processing_version=like.processing_version if like else "replay",
              status="ready", created_at=now, ready_at=now, revised_at=now,
              title=like.title if like else None, channel_id=like.channel_id if like else None,
              sheet=args.sheet, published_at=wall(0.0), kind="practice", format="fours")
    job = Job(id=slug.new_job_id(), run_id=run.id, state="done", created_at=now, run_after=now,
              kind="practice", finished_at=now, phase="done", fraction=1.0)
    watch = PracticeWatch(id=slug.new_slug(slug.SHORT_BYTES, "pw_"), sheet=args.sheet,
                          video_id=args.video, run_id=run.id, job_id=job.id, created_at=now,
                          status="ended", revised_at=now, recorded_s=doc.get("recorded_s"),
                          throws=len(doc["throws"]), busy_s=doc.get("busy_s"))
    session = PracticeSession(id=slug.new_slug(slug.SHORT_BYTES, "ps_"), user_id=args.user,
                              sheet=args.sheet, watch_id=watch.id, started_at=wall(args.from_s),
                              hand=args.hand, ended_at=wall(args.to_s), end_reason="stop")
    print(f"run {run.id}, job {job.id}, watch {watch.id}, session {session.id}")
    print(f"session {session.started_at:%H:%M:%S}-{session.ended_at:%H:%M:%S} UTC, "
          f"{len(doc['throws'])} throw(s) in the document")
    if args.dry_run:
        return 0

    upload_clips(doc, args.clips, args.video, run.id)
    key = practice_key(args.video, run.id)
    path = os.path.join(os.path.dirname(os.path.abspath(args.doc)), "practice.import.json")
    with open(path, "w") as f:
        json.dump(doc, f)
    subprocess.run(["gcloud", "storage", "cp", "--content-type=application/json", path,
                    f"gs://{BUCKET}/{key}"], check=True)
    repo.put_run(run)
    repo.put_job(job)
    repo.put_watch(watch)
    repo.put_session(session)
    print(f"https://curling.dimmit.net/practice/session/{session.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
