# Validation data

`ground_truth.json` holds observations made by eye against the video, plus the
wall scoreboard as read from the stream.

It is small, and it is the most valuable data in the project. Every automated
measure of recall so far has been circular: the YOLO model was trained on
labels written by the classical detector, so a benchmark against those labels
measures agreement rather than accuracy. Breaking that loop needs observations
made by a person, and each one here has already paid for itself:

- The red at 179 s exposed the collision hand-off — the tracker follows the
  struck stone, because a stone stopping dead looks like a track dying.
- The yellow at 233 s exposed that the side lines lie *outside* the panel, so
  the test for a shooter rolling out could never be true.
- The yellow guard at 10061 s exposed a whole class of delivery the design
  could not represent: one that comes to rest before entering the view, so it
  has no flight to follow.
- The sweeper at 3153.9 s showed that a dropped candidate's plausibility must
  be judged against kept deliveries of *either* colour. Judged by colour alone
  it looked like an isolated drop and ranked fourth on the review page.
- The house at 3132 s showed that ends were being scored from the wrong moment.

## Adding to it

`curling-score review <url>` builds a page of short windows worth checking,
most likely problem first. When you confirm or reject one, add it here with
enough of a note to say *what* was seen, not just that something was. The notes
are what make an entry reusable once the code around it has changed.
