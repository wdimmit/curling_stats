"""A club's switching broadcast, mapped into the canonical three-panel layout.

Some clubs' streams (DFW's) cut between camera layouts and carry graphics;
nothing downstream reads them raw. A club profile names a layout
(`clubs.Club.broadcast_layout`): `layout` describes it and maps one frame,
`convert` maps a whole video (a VOD after download, or a live recording as it
grows), and `vod` converts a downloaded video in place, once.
"""
