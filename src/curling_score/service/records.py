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
    overrides: dict = field(default_factory=dict)
    overrides_version: int = 0
    submitter_ip_hash: str | None = None

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
