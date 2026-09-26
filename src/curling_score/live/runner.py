"""Driving a live session until its stream is over.

The hosted worker interleaves several sessions on one GPU; this is the single
stream case, for replaying a cached video as if it were live from the command
line.
"""

import time


def run_session(session, *, sleep=time.sleep, idle_s: float = 2.0):
    """Step ``session`` until it is done, waiting ``idle_s`` whenever it has
    nothing to do -- the recording has to grow before anything else settles."""
    while not session.done:
        if session.step() is None and not session.done:
            sleep(idle_s)
