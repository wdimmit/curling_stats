"""The records the service keeps, and what each one is for.

A *run* is one pass of the pipeline over one video (or a window of it). A
*source* is one game, the thing a person means by "this game". A *chart* is
one link, pinned to the run that produced its game, carrying everything the
people holding that link have entered. Jobs queue runs for the worker; the
rest is bookkeeping.

Plain dataclasses with plain values -- strings, numbers, datetimes, lists,
dicts -- so any document store can hold them without a mapping layer.
"""

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime

RUN_STATUSES = ("pending_approval", "queued", "processing", "ready", "failed")
JOB_STATES = ("queued", "running", "done", "failed")
FAIL_KINDS = ("transient", "blocked", "permanent")


def _from_dict(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Run:
    id: str
    video_id: str
    processing_version: str
    status: str
    created_at: datetime
    window_start_s: float | None = None
    window_end_s: float | None = None
    title: str | None = None
    channel_id: str | None = None
    duration_s: float | None = None
    sheet: int | None = None
    published_at: datetime | None = None
    games: list = field(default_factory=list)   # [{index, start_s, end_s, ends}]
    playlist_id: str | None = None
    league: str | None = None
    timeline_key: str | None = None
    meta_key: str | None = None
    error: str | None = None
    ready_at: datetime | None = None

    def covers(self, start_s: float | None) -> bool:
        """Whether the part of the video this run analysed includes ``start_s``."""
        if self.window_start_s is None:
            return True
        if start_s is None:
            return False
        return self.window_start_s <= start_s <= (self.window_end_s or float("inf"))

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class Job:
    id: str
    run_id: str
    state: str
    created_at: datetime
    run_after: datetime
    attempts: int = 0
    max_attempts: int = 4
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    phase: str | None = None
    fraction: float | None = None
    message: str | None = None
    progress_at: datetime | None = None
    phase_started_at: datetime | None = None
    # Seconds each finished phase took, so the page can show where the time
    # went rather than only where the job is.
    phase_timings: dict = field(default_factory=dict)
    error: str | None = None
    error_kind: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class Source:
    id: str
    video_id: str
    game_start_s: float
    game_end_s: float
    current_run_id: str
    game_index: int
    created_at: datetime
    title: str | None = None
    sheet: int | None = None
    league: str | None = None
    played_at: datetime | None = None
    # Who played, by the colour they threw. Per game rather than per video:
    # the league is the same all night, the teams are not. Set by hand -- the
    # detector reads stones, not scoreboards -- and served into the timeline,
    # where the report has been headed "red" and "yellow" for want of them.
    team_red: str | None = None
    team_yellow: str | None = None

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class Chart:
    id: str
    share_slug: str
    video_id: str
    run_id: str
    created_at: datetime
    updated_at: datetime
    requested_start_s: float | None = None
    requested_sheet: int | None = None
    source_id: str | None = None
    game_index: int | None = None
    snap_distance_s: float | None = None
    # The team this chart belongs to, if any. NOT an access-control field:
    # /c/ stays writable by anyone holding it, signed in or not, and that is
    # deliberate. What it decides is which chart teammates land on and whose
    # list it shows up in. Beware `where("team_id", "==", None)` -- Firestore
    # treats an absent field and a null one as different, so that query misses
    # every chart written before this field existed.
    team_id: str | None = None
    owner_user_id: str | None = None
    # This chart's link now shows another chart: the team already had one for
    # this game. The link keeps working -- lookup follows the pointer -- so
    # nobody's bookmark ever breaks.
    superseded_by: str | None = None
    # The same collision, but this chart already had grading in it. Two
    # people's charting is never merged automatically; a banner is recoverable
    # and a bad merge is not.
    duplicate_of: str | None = None
    overrides: dict = field(default_factory=dict)
    # Who last touched each override key, and when: {key: {"by", "at"}}. Kept
    # beside the overrides rather than inside them because apply_overrides
    # splats a patch straight onto the shot, so anything stored in there would
    # flow into export.json and on into training labels. `at` is an ISO string
    # -- restore.py's field revival is one level deep and will not descend.
    overrides_meta: dict = field(default_factory=dict)
    overrides_version: int = 0
    submitter_ip_hash: str | None = None

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class User:
    """Somebody who signed in.

    ``id`` is the Firebase UID, not the Google ``sub``: it stays put if a
    second sign-in provider is ever linked, and it is what every Firebase API
    speaks. Using it as the document id means no lookup table.
    """

    id: str
    created_at: datetime
    email: str | None = None
    email_verified: bool = False
    name: str | None = None
    picture_url: str | None = None
    last_seen_at: datetime | None = None

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class Team:
    """A few people who chart together, and the games they share.

    Members are a list on the team rather than documents of their own. The
    question asked most often is "is this person on this chart's team", and the
    team id comes from the chart -- so this answers it with one read of a
    document we usually already have, and ArrayUnion/ArrayRemove make joining
    and leaving atomic without a transaction. Reach for membership documents
    when roles or joined-at dates are wanted; the repo's team_members() and
    teams_for_user() exist so that swap stays a two-method change.
    """

    id: str
    name: str
    owner_user_id: str
    created_at: datetime
    member_ids: list = field(default_factory=list)

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class Invite:
    """A link that adds whoever opens it to a team."""

    id: str
    team_id: str
    created_by_user_id: str
    created_at: datetime
    expires_at: datetime | None = None
    max_uses: int | None = None
    uses: int = 0
    revoked_at: datetime | None = None

    def usable(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= now:
            return False
        return self.max_uses is None or self.uses < self.max_uses

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class Worker:
    id: str
    last_seen_at: datetime
    version: str | None = None
    model_id: str | None = None
    gpu: str | None = None
    current_job_id: str | None = None

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class WatchedPlaylist:
    id: str
    playlist_id: str
    label: str
    created_at: datetime
    enabled: bool = True
    last_polled_at: datetime | None = None
    last_seen_video_ids: list = field(default_factory=list)
    failures: int = 0
    last_error: str | None = None

    to_dict = asdict
    from_dict = classmethod(_from_dict)
