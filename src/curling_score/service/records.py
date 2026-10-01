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

# "live": a stream still being played, with the ends settled so far published.
RUN_STATUSES = ("pending_approval", "queued", "processing", "live", "ready",
                "failed")
# A job is a recording to analyse whole, or a stream to follow while it plays.
# Live jobs are claimed first; a record from before kinds is a recording.
JOB_KINDS = ("vod", "live")
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
    # "fours" or "doubles"; None is a record from before formats, read as fours
    format: str | None = None
    # "vod" or "live", as on the run's job. A live run is published end by
    # end while its stream plays; `revised_at` changes with every publish,
    # the way `ready_at` does at completion, so each is served fresh.
    kind: str = "vod"
    revised_at: datetime | None = None
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
    kind: str = "vod"

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
    # "fours" or "doubles"; None is a record from before formats, read as fours
    format: str | None = None
    played_at: datetime | None = None
    # Who played, by the colour they threw. Per game rather than per video:
    # the league is the same all night, the teams are not. Set by hand -- the
    # detector reads stones, not scoreboards -- and served into the timeline,
    # where the report has been headed "red" and "yellow" for want of them.
    team_red: str | None = None
    team_yellow: str | None = None
    # Where the game actually starts, once somebody has told us -- and how
    # many ends are left once the warm-up in front of it is dropped.
    #
    # Club streams open with practice. The sheet never sits empty for
    # GAME_GAP_S while players are sliding rocks, so segment_games cannot call
    # it a separate game, and each block of it clears MIN_END_S, so it arrives
    # as leading ends of the real one. Nothing in the video says where the
    # practice stops; the person submitting does, in "Game starts at".
    #
    # It lives here rather than on the Chart that carried the time because the
    # practice belongs to the *game*. Keeping it per chart left the review
    # link, the catalogue and every later chart of the same game still showing
    # the warm-up, with only the one chart whose owner happened to type a time
    # coming out clean.
    play_start_s: float | None = None
    play_ends: int | None = None
    # The YouTube playlist the club published the stream in, which is the
    # club's own name for the league ("2025-2026 Tuesday Super League"). Found
    # by playlist_index, never typed: `league` above is the label people edit,
    # and it drifts ("Super", "Spring Thursday") where this does not.
    playlist_id: str | None = None
    playlist_title: str | None = None
    # Both teams' thinking time for the game as the review link serves it --
    # after the warm-up is trimmed -- so the thinking report can rank a league
    # without loading a 1 MB timeline per game. Keyed by the run and the play
    # start it was read from; when either moves it is stale until refreshed.
    thinking: dict | None = None
    # This page's game is now part of another page's: a newer run read two
    # games as one (a pause the empty sheet took for a changeover, joined by
    # the board). Hidden from the catalogue, and /g/ follows the pointer; a
    # later run that splits the game again takes the page back.
    merged_into: str | None = None
    # Scores people typed in for the ends the wall board never gave one, by
    # the game's end number as a string: {"4": {"red", "yellow", "by", "at"}}.
    # On the game, like the team names, so every chart of it shows them.
    # Gaps only: timeline.apply_entered_scores never lets one beat the board.
    entered_scores: dict = field(default_factory=dict)

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
    # When to look. Weekly windows in the club's time, e.g. league night:
    # [{"days": ["thu"], "start": "18:00", "end": "23:30"}]. Inside one the
    # playlist is polled every `live_poll_s`, to catch a stream as it goes
    # live; outside, every `idle_poll_s`, or never when that is None.
    schedule: list = field(default_factory=list)
    live_poll_s: float = 180.0
    idle_poll_s: float | None = 3600.0

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class PlaylistIndexEntry:
    """One of a channel's YouTube playlists, and the videos in it.

    YouTube lists a playlist's videos but has no call for a video's playlists,
    so the only way to learn which league a stream was published under is to
    index the channel. A cache of YouTube, rebuilt by playlist_index.refresh,
    so the backup leaves it out.
    """

    id: str                     # YouTube's playlist id
    channel_id: str
    title: str
    item_count: int
    scanned_at: datetime
    video_ids: list = field(default_factory=list)

    to_dict = asdict
    from_dict = classmethod(_from_dict)


@dataclass
class Flag:
    """Somebody pointed at a rock and said what is wrong with it.

    `where` is worked out by the server from the link the flag was sent from
    -- chart, source, run, video, pipeline version -- and is never taken from
    the browser; `place` is what the browser showed. Kept out of the chart
    entirely: overrides flow into export.json and on into training labels,
    and a complaint about a chart is not a label.
    """

    id: str
    created_at: datetime
    note: str
    where: dict = field(default_factory=dict)
    place: dict = field(default_factory=dict)
    status: str = "open"                  # "open" | "resolved"
    resolved_at: datetime | None = None
    overrides_version: int | None = None
    user: dict | None = None              # {"uid", "email"} when signed in
    ip_hash: str | None = None

    to_dict = asdict
    from_dict = classmethod(_from_dict)
