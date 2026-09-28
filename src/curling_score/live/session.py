"""One live stream, from its first minutes to its last end.

A session owns a growing recording and turns it into a timeline one end at a
time. Each call to :meth:`LiveSession.step` does the single most useful thing
there is to do, and says what it did:

* **Calibrate** once fifteen minutes are recorded, from keyframes spread over
  what exists, and again every fifteen minutes until the calibration holds
  still. Measured on 17 cached videos, calibrating from the first 10-60
  minutes moves stone positions by at most ~1.4 cm against the whole video,
  but twice before 30 minutes a panel's hog paint was missed, and once a side
  camera's tee row came out 27 px off -- so a first calibration is used, not
  trusted, until two in a row agree.
* **Follow the profile** -- the keyframe stone counts -- as the file grows.
* **Build the next settled end** (:func:`segment.settled_ends`), strictly in
  order, since each end's run-up begins where the previous one closed. Then
  read the open game's board again and publish.
* **Finish** when the stream ends: settle and build what is left, take the
  final board reads, publish the last document.

The session never decides *when* it runs: the worker's lane calls
:meth:`next_end_due` across sessions and steps the one whose end has waited
longest. Everything that touches video goes through ``pipeline``, so the
session's own decisions are testable without any.
"""

import logging
from copy import deepcopy
import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone

from curling_score import analyze, timeline
from curling_score.game import segment
from curling_score.game.segment import EndSegment, GameSegment
from curling_score.geometry.calibrate import CalibrationError

log = logging.getLogger(__name__)

# The first calibration waits for this much footage, then repeats this often
# until two calibrations in a row agree. By the last, a stream with no working
# calibration is given up, and one with a working calibration keeps it.
CALIB_FIRST_S = 900.0
CALIB_EVERY_S = 900.0
CALIB_GIVE_UP_S = 3600.0
# A stream that has ended with no calibration gets this many more tries.
ENDED_CALIB_TRIES = 2
# Two side-view calibrations agree when their tee and hog rows are this close.
SIDE_AGREE_PX = 3.0
# The profile is extended when the recording has grown this much.
PROFILE_EVERY_S = 30.0
# An open game's board is read this far behind the write head: a board read
# looks 90 s either side of a moment 60 s before the end it is given.
BOARD_BEHIND_S = 90.0


class LiveError(RuntimeError):
    """The stream cannot be processed live; the recording's VOD path takes it."""


@dataclass
class Calibration:
    """Panels, their calibrations and the side views, from ``[0, until_s]``."""

    panels: object
    setups: dict
    sideviews: dict | None
    until_s: float
    # False when the side views were left out on purpose (SKIP_LONGVIEW):
    # then there is nothing of them to wait for.
    side_expected: bool = True

    @property
    def complete(self) -> bool:
        """Both hog lines found and, where wanted, both side views calibrated
        across."""
        if not all(s.hog_line is not None for s in self.setups.values()):
            return False
        if not self.side_expected:
            return True
        return (self.sideviews is not None
                and all(v.has_lateral for v in self.sideviews.values()))

    def agrees_with(self, other) -> bool:
        if other is None or not other.complete or not self.complete:
            return False
        if not self.side_expected:
            return True
        return all(abs(v.tee_row - other.sideviews[n].tee_row) <= SIDE_AGREE_PX
                   and abs(v.hog_row - other.sideviews[n].hog_row) <= SIDE_AGREE_PX
                   for n, v in self.sideviews.items())


@dataclass
class Models:
    """The detectors, loaded once and shared by every session in a worker."""

    detector: object = None
    broom_model: object = None
    line_model: object = None


def _side_worth(cal) -> int:
    """How much a calibration's side views read: one for each view, one
    more for each calibrated across."""
    if cal is None or cal.sideviews is None:
        return 0
    return sum(1 + bool(v.has_lateral) for v in cal.sideviews.values())


class LiveSession:
    def __init__(self, video_id, url, recording, fmt, pipeline, *, sheet=None,
                 models=None, publish=None, progress=log.info,
                 now=lambda: datetime.now(timezone.utc)):
        self.video_id, self.url, self.sheet, self.fmt = video_id, url, sheet, fmt
        self.recording, self.pipeline = recording, pipeline
        self.models = models or Models()
        self.publish = publish or (lambda doc: None)
        self.progress, self.now = progress, now
        self.min_end_s = fmt.delivered_per_end * segment.MIN_DELIVERY_GAP_S

        self.calibration = None     # the newest one that worked
        self.steady = False         # two complete calibrations in a row agreed
        self.next_calibration_s = CALIB_FIRST_S
        self.samples = []
        self.profiled_to = None     # t of the last keyframe in the profile
        self.games = {}             # index -> newest settled GameSegment
        self.built = {}             # (game, end) -> built end dict
        self.ends = {}              # (game, end) -> EndSegment it was built from
        self.boards = {}            # game index -> newest BoardRead
        self.prev_end_s = None
        self.done = False
        self._ended_calib_tries = 0

    # --- what the lane asks -------------------------------------------------

    def next_end_due(self):
        """When the next end to build finished (its ``end_s``), or None when
        there is none ready. The lane builds the one that has waited longest."""
        if self.done or self.calibration is None:
            return None
        self._extend_profile()
        end = self._next_end()
        return None if end is None else end[1].end_s

    def needs_calibration(self) -> bool:
        """Whether this stream is waiting for its first calibration -- which
        blocks every end it will have, so the lane does it before anything
        else. Improving a calibration it already has is not urgent."""
        if self.done or self.calibration is not None:
            return False
        return self._calibration_due(self.recording.head_s(), self.recording.ended())

    def step(self):
        """Do the most useful thing there is; return what, or None if nothing."""
        if self.done:
            return None
        head, ended = self.recording.head_s(), self.recording.ended()
        if ended and self.calibration is None and (
                head <= 0 or self._ended_calib_tries >= ENDED_CALIB_TRIES):
            raise LiveError("the stream ended before it could be calibrated"
                            if head > 0 else "the stream ended with nothing recorded")
        if self._calibration_due(head, ended):
            return self._calibrate(head)
        if self.calibration is None:
            return None
        self._extend_profile()
        nxt = self._next_end()
        if nxt is not None:
            self._build(*nxt)
            return "end"
        if ended and self.profiled_to is not None:
            return self._finish()
        return None

    # --- calibration ----------------------------------------------------------

    def _calibration_due(self, head, ended):
        if self.steady:
            return False
        if self.calibration is not None and head >= CALIB_GIVE_UP_S:
            return False          # a working calibration is kept after an hour
        if self.calibration is None and ended:
            return True           # a stream shorter than the wait still gets one
        return head >= self.next_calibration_s

    def _calibrate(self, head):
        self.next_calibration_s = head + CALIB_EVERY_S
        if self.recording.ended():
            self._ended_calib_tries += 1
        try:
            got = self.pipeline.calibrate(self.recording.path, head)
        except CalibrationError as exc:
            self.progress(f"calibration from {head:.0f} s failed: {exc}")
            if self.calibration is None and head >= CALIB_GIVE_UP_S:
                raise LiveError(f"no calibration after {head:.0f} s: {exc}") from exc
            return "calibration failed"
        if (got.side_expected and self.calibration is not None
                and _side_worth(got) < _side_worth(self.calibration)):
            # The panels are newer; the side views that worked are kept. On
            # sheet 3, 2026-09-27, a recalibration that lost both views took
            # the splits from every end after it. Kept views prove nothing
            # about the new ones, so this is not steady: it goes on trying.
            self.calibration = dataclasses.replace(
                got, sideviews=self.calibration.sideviews)
            self.steady = False
            self.progress(f"calibrated from {head:.0f} s, keeping the side "
                          "views before it: the new ones read less")
            return "calibrated"
        self.steady = got.agrees_with(self.calibration)
        self.calibration = got
        self.progress(f"calibrated from {head:.0f} s"
                      + (", steady" if self.steady else ""))
        return "calibrated"

    # --- profile and ends -----------------------------------------------------

    def _extend_profile(self):
        head = self.recording.head_s()
        if (self.profiled_to is not None and not self.recording.ended()
                and head - self.profiled_to < PROFILE_EVERY_S):
            return
        new = self.pipeline.samples(self.recording.path, self.calibration.setups,
                                    self.profiled_to, head)
        new = [s for s in new if self.profiled_to is None or s.t > self.profiled_to]
        if new:
            self.samples.extend(new)
            self.profiled_to = new[-1].t
        elif self.profiled_to is None:
            self.profiled_to = 0.0

    def _settled(self):
        ended = self.recording.ended()
        games = segment.settled_ends(self.samples, self.min_end_s, ended=ended)
        for game in games:
            self.games[game.index] = game
        return games

    def _next_end(self):
        for game in self._settled():
            for end in game.ends:
                if (game.index, end.number) not in self.built:
                    return game, end
        return None

    def _build(self, game, end):
        ctx = analyze.EndContext(
            path=self.recording.path, read_path=self.recording.path,
            read_setups=self.calibration.setups,
            sideviews=self.calibration.sideviews,
            detector=self.models.detector, broom_model=self.models.broom_model,
            line_model=self.models.line_model, fmt=self.fmt, use_cache=False,
            one_pass=True, progress=self.progress)
        built, self.prev_end_s = self.pipeline.build_end(ctx, game, end,
                                                         self.prev_end_s)
        self.built[(game.index, end.number)] = built
        self.ends[(game.index, end.number)] = end
        self._read_board(game)
        self.publish(self.document())

    def _read_board(self, game):
        # A closed game's board is read the way analyze() reads it, from the
        # game's own end. An open game's board is read as late as the
        # recording allows: the club posts cards late, often an end or more
        # behind the play.
        read = game if game.closed else GameSegment(
            index=game.index, start_s=game.start_s,
            end_s=self.recording.head_s() - BOARD_BEHIND_S,
            ends=game.ends, closed=False)
        got = self.pipeline.read_board(self.recording.path, read)
        if got is not None:
            self.boards[game.index] = got

    def _finish(self):
        if self._next_end() is not None:
            return None
        for game in self.games.values():
            if game.ends and any((game.index, e.number) in self.built for e in game.ends):
                self._read_board(game)
        self.done = True
        self.publish(self.document())
        return "finished"

    # --- the document ---------------------------------------------------------

    def _board_for(self, index, n_ends):
        """The game's newest board read, scored against the ends it has now.

        A read keeps its cards, but which ends are unread -- and whether there
        is a final -- depends on how many ends the game has. A read taken five
        ends in, kept because the end-of-game read failed, would otherwise
        still say every end was posted and carry its five-end total as the
        final of a nine-end game (found comparing live with VOD on
        jgZ9wlxGYHM). A fresh block every time, since finish_game writes to it.
        """
        from curling_score.game import scoreboard as sb

        board = self.boards.get(index)
        if board is None:
            return None
        try:
            scores = sb.per_end_from_cards(board.got.board, n_ends)
        except sb.ScoreboardError as exc:
            self.progress(f"game {index + 1}: the board read does not fit {n_ends} ends: {exc}")
            return None
        got = sb.GameBoard(scores=scores, read_at_s=board.got.read_at_s,
                           reads=board.got.reads, board=board.got.board)
        return analyze.board_block(got)

    def document(self) -> dict:
        """The timeline as it stands: every built end, the boards' newest
        scores on them, and a ``live`` block saying how far it has got."""
        head = self.recording.head_s()
        out_games = []
        for index in sorted({g for g, _ in self.built}):
            game = self.games[index]
            keys = sorted(k for k in self.built if k[0] == index)
            board = self._board_for(index, len(keys))
            ends = []
            for key in keys:
                end = deepcopy(self.built[key])
                score = None if board is None else board.scores.per_end.get(key[1])
                end["score"] = None if score is None else dict(score)
                end["score_source"] = None if score is None else "board"
                ends.append(end)
            segs = [self.ends[k] for k in keys]
            seg = GameSegment(index=index, start_s=game.start_s, end_s=segs[-1].end_s,
                              ends=segs, closed=game.closed)
            out = analyze.finish_game(seg, ends, board, self.fmt, self.progress)
            out["in_progress"] = not (self.done or (game.closed and len(keys) == len(game.ends)))
            out_games.append(out)
        cal = self.calibration
        doc = timeline.build_document(
            video_id=self.video_id, url=self.url, sheet=self.sheet,
            duration_s=head,
            calibration=({} if cal is None
                         else analyze.calibration_block(cal.setups, cal.sideviews)),
            games=out_games, window=(None, None),
            processing_version=self.pipeline.processing_version(
                None if cal is None else cal.sideviews),
            fmt=self.fmt, check=timeline.format_check(out_games, self.fmt))
        doc["live"] = {"in_progress": not self.done, "recorded_s": round(head, 1),
                       "updated_at": self.now().isoformat()}
        return doc


class VideoPipeline:
    """The session's pipeline for real: every call reads the recording.

    Detection reads the growing recording itself -- no strip proxy, which is
    built from a finished file -- and never the detection cache, which keys
    on the file's size (see ``analyze.EndContext``).
    """

    def __init__(self, *, weights=None, skip_longview=False, line=True,
                 progress=log.info):
        self.weights, self.skip_longview = weights, skip_longview
        self.line, self.progress = line, progress

    def calibrate(self, path, until_s):
        from curling_score.geometry import layout
        from curling_score.ingest import frames as F

        frames = F.spread_keyframes(path, until_s, count=analyze.CALIB_FRAMES)
        if len(frames) < 2:
            raise CalibrationError(f"{len(frames)} keyframe(s) in the first "
                                   f"{until_s:.0f} s; need at least two")
        try:
            panels, setups, sideviews = analyze.calibrate_from(
                frames, skip_longview=self.skip_longview, progress=self.progress)
        except layout.LayoutError as exc:
            raise CalibrationError(f"panels not found: {exc}") from exc
        return Calibration(panels=panels, setups=setups, sideviews=sideviews,
                           until_s=until_s, side_expected=not self.skip_longview)

    def samples(self, path, setups, from_s, until_s):
        from curling_score.game import profile
        from curling_score.ingest import frames as F

        return profile.build_profile(
            path, setups, sweep=F.keyframe_sweep(path, start_s=from_s, end_s=until_s))

    def build_end(self, ctx, game, end, prev_end_s):
        # Built without a board score: the board is read again after every
        # end, and the session puts its newest scores on every end it publishes.
        return analyze.build_one_end(ctx, game, end, prev_end_s, board_score=None)

    def read_board(self, path, game):
        return analyze.board_for_game(path, game, progress=self.progress)

    def processing_version(self, sideviews):
        from curling_score import version
        from curling_score import weights as weights_mod

        return version.processing_version(
            self.weights, weights_mod.side_path(), weights_mod.broom_path(),
            line=self.line and sideviews is not None)
