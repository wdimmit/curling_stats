"""Driving one live session until its stream is over."""

from curling_score.live import runner


class Recording:
    def __init__(self, length_s):
        self.length_s, self.head = length_s, 0.0

    def head_s(self):
        return self.head

    def ended(self):
        return self.head >= self.length_s


class Session:
    """Does one thing per 300 s of new footage; done once the stream ends."""

    def __init__(self, rec):
        self.rec, self.did_at, self.done = rec, [], False

    def step(self):
        if self.rec.ended():
            self.done = True
            return "finished"
        if not self.did_at or self.rec.head - self.did_at[-1] >= 300:
            self.did_at.append(self.rec.head)
            return "end"
        return None


def test_it_steps_while_there_is_work_and_waits_when_there_is_none():
    rec = Recording(1000.0)
    s = Session(rec)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        rec.head += 100.0

    runner.run_session(s, sleep=sleep, idle_s=2.0)
    assert s.done
    assert s.did_at == [0.0, 300.0, 600.0, 900.0]
    assert waits and set(waits) == {2.0}
