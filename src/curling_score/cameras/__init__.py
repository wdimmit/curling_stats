"""Practice on the club's own NDI cameras rather than its YouTube stream.

libndi (in the cyndilib wheel) receives each camera's H.264, and a stand-in
for the libavcodec it decodes with hands those packets on untouched (``tap``),
so a sheet's four cameras can be composed into one recording without anything
decoding them twice.

See docs/superpowers/specs/2026-10-24-practice-direct-feeds-design.md.
"""
