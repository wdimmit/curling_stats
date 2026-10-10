"""A practice session copied into someone else's account.

The copy is a second session on the same watch over the same window -- what
two people starting on one sheet get anyway -- so it reads the same throws.
It takes the original's hidden throws as they stand, and the hand given for
the other person (the original's is the thrower's own).

    AT=$(gcloud auth print-access-token) python scripts/practice/copy_session.py \\
        ps_0o8DrTmt5lss1APHG --email someone@example.org --hand right

They must have signed in to the site once: that is what makes the account.
Needs the caller's own gcloud login in $AT: there is no admin endpoint for this.
"""

import argparse
import os
import sys
from dataclasses import replace

from curling_score.service import slug

PROJECT = "curling-stats-508323"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session", help="the session to copy")
    ap.add_argument("--email", required=True, help="the account to copy it into")
    ap.add_argument("--hand", choices=("left", "right"), default="right")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from google.cloud import firestore
    from google.oauth2.credentials import Credentials

    from curling_score.service.firestore_repo import FirestoreRepo

    db = firestore.Client(project=PROJECT, credentials=Credentials(os.environ["AT"]))
    repo = FirestoreRepo(client=db)
    src = repo.get_session(args.session)
    if src is None:
        sys.exit(f"no session {args.session}")
    if src.active:
        sys.exit(f"{args.session} is still running; copy it once it has ended")
    users = [d.id for d in db.collection("users")
             .where(filter=firestore.FieldFilter("email", "==", args.email.lower())).stream()]
    if len(users) != 1:
        sys.exit(f"{len(users)} accounts for {args.email}: they need to sign in to the site once")
    (uid,) = users
    if any(s.watch_id == src.watch_id and s.started_at == src.started_at
           for s in repo.sessions_for_user(uid)):
        sys.exit(f"{args.email} already has this session")
    copy = replace(src, id=slug.new_slug(slug.SHORT_BYTES, "ps_"), user_id=uid,
                   hand=args.hand, hidden=list(src.hidden))
    print(f"{copy.id}: sheet {copy.sheet}, {copy.started_at:%H:%M}-{copy.ended_at:%H:%M} UTC, "
          f"{len(copy.hidden)} hidden, {copy.hand}-handed")
    if not args.dry_run:
        repo.put_session(copy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
