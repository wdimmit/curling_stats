"""Build a training set from many games rather than a few.

Everything before this package sampled one VOD densely: ds10 holds 6,995 frames
taken 2 s apart inside 20 ends of 5 games, which is a few hundred genuinely
distinct house configurations dressed up as seven thousand. This package does
the opposite -- a handful of frames from each of 130 games, one club season --
and hands them to a person to label rather than to the detector that wrote the
last ten datasets.
"""
