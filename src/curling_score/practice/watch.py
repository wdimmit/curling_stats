"""One sheet's practice, watched: every delivery reported as it comes to rest.

A `PracticeWatch` owns a growing recording of one sheet -- its cameras from
Start, or a recording replayed (`practice-replay`) -- and turns it into a list
of throws. Each call to :meth:`step` does the one most useful
thing there is to do, and says whether it did anything:

* **Calibrate** once the recording, which begins at Start, holds something
  to read (READ_MIN_S: at first there is no file).
  A calibration missing a hog line or a side view is tried again every
  CALIB_RETRY_S, keeping the best, until CALIB_TRIES. A watch given a saved
  calibration (``cameras/calstore.py``) first
  checks it against the house rings in the first frames: if they fit, it is
  adopted and there is nothing to calibrate. A house it cannot read (players
  standing in it) is checked again on later frames, and if it never can be,
  the saved calibration is used all the same. Only a ring found elsewhere (a
  camera moved) runs that loop instead. A watch keeps a calibration only for a
  sheet that has none: where it is given somewhere to keep it, the loop's first
  complete calibration seeds the sheet, unless the side views were left out on
  purpose. It never replaces one; ``cameras-calibrate`` on an empty sheet does.
* **Watch:** detect both panels from where the last step stopped to just
  behind the head, at most STEP_MAX_S at a time. Find the arrivals each panel
  confirmed and the releases each saw, pair them, measure each new throw
  (`practice.enrich`), and publish.
* **Finish** once the recording has ended and been read to its end.

Everything that touches video goes through ``pipeline``, so the watch's own
decisions are testable without any.
"""

import logging
import time

from curling_score.cameras.calstore import HouseCheck
from curling_score.geometry.calibrate import CalibrationError
from curling_score.live.session import _worth
from curling_score.practice import throws
from curling_score.practice.enrich import OTHER_HOUSE
from curling_score.practice.finder import ArrivalFinder, Buffer
from curling_score.practice.releases import ReleaseBook

log = logging.getLogger(__name__)

HOUSES = ("top", "bottom")
# Both panels, twice the release finder's rate (it is thinned back for that).
DETECT_FPS = 10.0
# The last packets of a growing MPEG-TS may not decode yet.
HEAD_MARGIN_S = 2.0
# Each read enters a TS file 10 s early (frames.seek_lead), so less new
# footage than this is not worth a read; a catch-up is read in pieces so no
# single step holds the lane for long.
STEP_MIN_S = 2.0
STEP_MAX_S = 30.0
# A recording that begins at Start (the cameras') holds nothing to read at
# first: no file until the composer has written one, then less than the two
# keyframes a calibration needs. Until its head is this far in there is
# nothing to do yet -- the composite's keyframes come one a second from 0, the
# cameras' first pictures reach it 0.8-1.2 s in (timed from the session's zero,
# taken before the captures start), so two keyframes with all four cameras by
# 2 s, behind the HEAD_MARGIN_S that may not decode yet.
READ_MIN_S = 4.0
CALIB_RETRY_S = 60.0
CALIB_TRIES = 5
# A saved calibration is checked once the recording holds this much. The
# composite has a keyframe a second (a 60-frame GOP at 60 fps), so this is about
# ten keyframes for the check's median: the more there are, the less likely
# that someone standing in the house outvotes the empty ice.
CHECK_MIN_S = 10.0
# A check that cannot read a house -- busy sheets hid a ring at 3 of 3 Starts
# on the Ryzen -- says nothing about the camera. It is made again on the frames
# recorded since, every CHECK_RETRY_S of recording, CHECK_TRIES times in all
# (the last at 55 s), and if none can read it the saved calibration is used.
CHECK_RETRY_S = 15.0
CHECK_TRIES = 4
# A throw nothing saw released is dated this long before its arrival, as the
# recorded pipeline does (timeline.RELEASE_TO_ARRIVAL_S).
RELEASE_TO_ARRIVAL_S = 15.0


def _thrown_at(arrival, rel) -> float:
    return rel.t if rel is not None else arrival.t_enter - RELEASE_TO_ARRIVAL_S


class PracticeWatch:
    def __init__(self, *, recording, pipeline, models, t0_s: float = 0.0,
                 wall_t0=None, publish=None, clipper=None, clock=time.monotonic,
                 progress=log.info, saved_calibration=None, save_calibration=None,
                 prior_throws=()):
        """The recording begins at Start; ``t0_s`` is where it begins on the
        stream's clock, which every published time is on.
        ``wall_t0`` is when the recording begins by the wall clock (epoch
        seconds), for a live stream whose own clock is not known.
        ``saved_calibration`` is the sheet's calibration from before, to be
        checked at Start. ``save_calibration`` is called with the first
        complete calibration this watch makes when there is no saved one --
        never for one made without the side views, and never to replace one.
        ``prior_throws`` are those an earlier watch of the same session
        published before its worker restarted, on the same clock: they lead
        this watch's document, and none of them is clipped here, since their
        footage is not in this recording. A clip still to be cut (False) is
        none coming (None) now; one that is up stays up."""
        self.rec, self.pipeline, self.models = recording, pipeline, models
        self.t0_s, self.wall_t0 = float(t0_s), wall_t0
        self._stop, self._stop_at = False, None
        # Lane seconds spent calibrating and reading: what a practice watch
        # costs the lane.
        self.busy_s = 0.0
        self.publish = publish or (lambda doc: None)
        self.clock, self.progress = clock, progress
        self.cal = None
        self.saved, self.save_calibration = saved_calibration, save_calibration
        self._saved_checked = False
        self._check_tries = 0
        self._check_from, self._check_at = 0.0, CHECK_MIN_S   # the next check's frames
        self.calib_tries = 0
        self.calib_next = float("-inf")
        self.status = "calibrating"
        self.done_s = 0.0
        self.buffers = {h: Buffer() for h in HOUSES}
        self.finders = self.books = None
        # A clip the old recording went without will never come: say so, or
        # the page waits for it for ever.
        self.throws: list = [{**th, "clip": None} if th.get("clip") is False else dict(th)
                             for th in prior_throws]
        self.done = False
        # Cuts each throw's clip once its footage is recorded (practice/clips.py).
        self.clipper = clipper
        self._clip_tried: set = {th["id"] for th in self.throws}

    def request_stop(self) -> None:
        """No session is watching any more: read what is recorded, then end."""
        self._stop = True

    def step(self) -> bool:
        if self.done:
            return False
        head, ended = self.rec.head_s(), self.rec.ended()
        if self.finders is None:
            if self._stop:
                self.status, self.done = "ended", True
                self._publish()
                return True
            return self._calibrate(head, ended)
        upto = head if ended else head - HEAD_MARGIN_S
        if self._stop:
            # Read to where the head was when told, not after it: nothing stops
            # the recorder until the job completes, and chasing a head that
            # keeps moving would end only by the luck of the segment cadence.
            if self._stop_at is None:
                self._stop_at = upto
            upto = min(upto, self._stop_at)
        if upto - self.done_s >= STEP_MIN_S or ((ended or self._stop) and upto > self.done_s):
            self._watch(min(upto, self.done_s + STEP_MAX_S))
            return True
        if ended or self._stop:
            self.status, self.done = "ended", True
            self._clip(final=True)
            self._publish()
            return True
        return False

    def document(self) -> dict:
        return {"practice": 1, "status": self.status, "t0_s": self.t0_s,
                "wall_t0": self.wall_t0, "busy_s": round(self.busy_s, 2),
                "recorded_s": round(self.t0_s + self.done_s, 2),
                "throws": list(self.throws)}

    # --- calibrating -------------------------------------------------------------

    def _calibrate(self, head, ended) -> bool:
        if self.clock() < self.calib_next:
            return False
        if head < READ_MIN_S and not ended:
            return False
        if self.saved is not None and not self._saved_checked:
            if head < self._check_at and not ended:
                return False
            return self._check_saved(head, ended)
        began = self.clock()
        try:
            cal = self.pipeline.calibrate(self.rec.path, head)
        except FileNotFoundError:
            return False                       # no file yet: nothing to read, no try spent
        except CalibrationError as exc:
            self.progress(f"practice: calibration {self.calib_tries + 1} failed: {exc}")
            cal = None
        finally:
            self.busy_s += self.clock() - began
        self.calib_tries += 1
        if cal is not None and (self.cal is None or _worth(cal) > _worth(self.cal)):
            self.cal = cal
        self.calib_next = self.clock() + CALIB_RETRY_S
        last_try = self.calib_tries >= CALIB_TRIES or ended
        if self.cal is not None and (self.cal.complete or last_try):
            if self.cal.complete:
                self._save(self.cal)
            self._adopt(self.cal)
        elif self.cal is None and last_try:
            self.status, self.done = "failed", True
        self._publish()
        return True

    def _check_saved(self, head, ended) -> bool:
        """The saved calibration against the first frames. Adopted if the
        house rings fit it. A house that cannot be read is checked again on the
        frames recorded since, and if none of CHECK_TRIES can read it, it is
        adopted all the same. Only a ring found off or the wrong size leaves it
        for the retry loop, which calibrates from this recording and never
        replaces it. One that is no longer complete (what that means can
        change) is never put on the ice, and is not worth reading frames to
        check. A read that fails is made again by the step that follows it:
        only an answer counts as a try."""
        if not self.saved.complete:
            self.progress("practice: the saved calibration is incomplete; calibrating "
                          "from this recording (cameras-calibrate on an empty sheet "
                          "replaces it)")
            self._saved_checked = True
            return True
        began = self.clock()
        try:
            got = self.pipeline.check_saved(self.rec.path, self._check_from, head, self.saved)
        except FileNotFoundError:
            return False                       # no file yet: the check is still owed
        finally:
            self.busy_s += self.clock() - began
        self._check_tries += 1
        if got is HouseCheck.UNREADABLE and self._check_tries < CHECK_TRIES and not ended:
            self.progress(f"practice: the saved calibration could not be checked "
                          f"(try {self._check_tries} of {CHECK_TRIES}, to {head:.0f} s); "
                          f"trying again on later frames")
            self._check_from, self._check_at = head, head + CHECK_RETRY_S
            return True
        self._saved_checked = True
        if got is HouseCheck.DISAGREES:
            log.warning("practice: the saved calibration does not fit the house rings (a "
                        "camera moved?); calibrating from this recording. The saved one is "
                        "kept: refresh it with cameras-calibrate on an empty sheet")
            return True
        if got is HouseCheck.UNREADABLE:
            log.warning("practice: the saved calibration could not be checked in %d tries "
                        "over the first %.0f s (the houses cannot be read); using it",
                        self._check_tries, head)
        else:
            self.progress("practice: the saved calibration fits; using it")
        self._adopt(self.saved)
        self._publish()
        return True

    def _adopt(self, cal) -> None:
        self.cal = cal
        self.finders = {h: ArrivalFinder(cal.setups[h]) for h in HOUSES}
        self.books = {h: ReleaseBook(cal.setups[h]) for h in HOUSES}
        self.status = "watching"

    def _save(self, cal) -> None:
        """Seed a sheet that has no saved calibration with the first complete
        one the retry loop makes: the loop ends with it, so this is once. Never
        over a saved one, which only cameras-calibrate on an empty sheet
        replaces, and not one made without the side views (skip_longview),
        which would stand in for one that has them. A failed save costs the
        save, not the watch."""
        if self.save_calibration is None or self.saved is not None or not cal.side_expected:
            return
        try:
            self.save_calibration(cal)
        except Exception as exc:  # noqa: BLE001 - the next Start tries again
            log.warning("practice: the calibration could not be saved: %r", exc)

    # --- watching ----------------------------------------------------------------

    def _watch(self, upto) -> None:
        began = self.clock()
        try:
            self._read(upto)
        finally:
            self.busy_s += self.clock() - began

    def _read(self, upto) -> None:
        got = self.pipeline.detect(self.rec.path, self.cal.setups, self.done_s, upto,
                                   self.models.detector)
        for h in HOUSES:
            self.buffers[h].extend(got[h])
            self.books[h].update(self.buffers[h].frames)
        self.done_s = upto
        new = []
        # Arrivals claim their releases before any release is given up on.
        for h in HOUSES:
            book = self.books[OTHER_HOUSE[h]]
            new += [(h, a, book.claim(a)) for a in self.finders[h].new(self.buffers[h])]
        for throwing in HOUSES:
            new += [(OTHER_HOUSE[throwing], None, r)
                    for r in self.books[throwing].unarrived(upto)]
        added = 0
        for house, arrival, rel in sorted(new, key=lambda n: _thrown_at(n[1], n[2])):
            shot = self.pipeline.enrich(
                self.rec.path, self.cal, self.models, house=house, arrival=arrival,
                release=rel, house_frames=self.buffers[house].frames,
                throw_frames=self.buffers[OTHER_HOUSE[house]].frames)
            th = throws.throw_record(shot, house=house, t0_s=self.t0_s)
            if self.clipper is None:
                th["clip"] = None              # no clip is coming
            self.throws.append(th)
            added += 1
        # The throw first, as soon as it is read; its clip follows.
        if added:
            self._publish()
        if self._clip():
            self._publish()

    def _clip(self, final: bool = False) -> bool:
        """Cut the clips whose footage is now recorded, oldest first; True if
        any throw's clip changed. Each throw is tried once: ``clip`` becomes
        True, or None when none is coming (no plan, or the cut or upload
        failed) so the page stops waiting. ``final``: the watch is ending, so a
        clip runs to where the recording stops rather than waiting. A clip
        that breaks costs that clip, never the watch; only a lost job ends it."""
        if self.clipper is None or self.cal is None:
            return False
        recorded = self.t0_s + self.done_s
        changed = False
        for th in self.throws:
            if th.get("clip") is not False or th["id"] in self._clip_tried:
                continue
            try:
                plan = self.clipper.plan(th, self.cal)
                if plan is not None and plan.end > recorded:
                    if not final:
                        continue
                    plan = plan.until(recorded)
                self._clip_tried.add(th["id"])
                made = plan is not None and self.clipper.make(th, plan, self.cal,
                                                              t0_s=self.t0_s)
            except Exception as exc:  # noqa: BLE001 - one clip, not the watch
                from curling_score.service.worker import Lost
                if isinstance(exc, Lost):
                    raise
                log.warning("practice: clip %s failed: %r", th["id"], exc)
                self._clip_tried.add(th["id"])
                made = False
            th["clip"] = True if made else None
            if made:
                try:
                    th["overlay"] = self.clipper.overlay(th, plan, self.cal)
                except Exception as exc:  # noqa: BLE001 - the clip stands without it
                    log.warning("practice: overlay %s failed: %r", th["id"], exc)
            changed = True
        return changed

    def _publish(self) -> None:
        self.publish(self.document())
