"""Processing a stream while it is still live, one end at a time.

A recorder writes the stream to a growing MPEG-TS file on the stream's own
clock (``recorder``, or ``replay`` for a cached video played back as if it
were live). A session per stream calibrates from the first minutes, follows
the activity profile as it grows, and builds each end once it has settled
(``session``), publishing the game one end at a time.
"""
