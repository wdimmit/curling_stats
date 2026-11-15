# Camera-direct phase 2 gate: results (2026-11-04)

The 11/02 sheet 3 SRT recording (Start 2026-11-02 19:22:00 PST, chart t 60-3250, 3190 s, game 1's first
three ends) was retimed into the capture's four files, composed offline into a YouTube-style composite, and
replayed both ways at speed 1 on the Ryzen (09:15-11:30 PST 11/04, ice empty).

## Inputs

- Retimed files: house_a 191,364 packets, house_h 191,366, long_a 191,368, long_h 191,368. Every file starts
  at 0.000 s; lengths 3189.994-3190.008 s.
- Composite: 3190.0 s (NVENC 20M, the composer's graphs), 28 min to make.
- Each replay: 53 min (speed 1, plus warm-up and drain).

## Numbers

| | Gate | Per view | Composite |
|---|---|---|---|
| Releases found | same throws | 47 | 47 |
| Throws only one run has | 0 | 1 (t_269.1) | 0 |
| Rest, stones stopped in the house (16 pairs) | median ≤2 cm, max ≤5 cm | median 0.5 cm, max 3.8 cm | |
| Rest, all 46 pairs | median ≤2 cm, max ≤5 cm | median 0.7 cm, max 171.2 cm | |
| Split | | 0 to +50 ms apart | |
| Latency p50 / p90 | p90 ≤20 s | 13.1 / 15.0 s | 12.9 / 14.4 s |
| House px/m, top / bottom | within 1% | 79.0 / 77.0 | 79.1 / 77.2 (0.13% / 0.26%) |

`compare_replays.py` as run printed `gate: FAIL (2 + 3 unmatched; rest)`. After its pairing fix (below) it
prints `gate: FAIL (0 + 1 unmatched; rest)`. **The gate as written fails.** What fails it:

1. **Rest of stones leaving the picture.** Every rest more than 5.2 cm apart (t_101.6 69 cm, t_186.3 50,
   t_1052.9 66, t_2217.9 171, t_2448.5 19, t_2872.0 16, t_2976.2 101, t_3170.7 43) is a stone read as `out`
   or `short` at the house panel's edge (|x| 1.1-1.8 m, or y past 2.7 m).
   - The frames of t_2217.9 show the stone sliding off the left of the house camera's picture between 2243.5
     and 2246 s. Neither run's "rest" is where it stopped; each is where that run last saw it.
   - The two pictures differ by the composite's re-encode, so the last frame a stone is detected in differs.
   - Stones that stopped in view agree: 16 pairs, median 0.5 cm, max 3.8 cm.
2. **One false positive, per view only.** t_269.1 is a "red stone short of the bottom house" with no
   release, at the panel's right edge. The bottom house frames 266-278 s show no stone, only players walking
   there.
   - It is a borderline detection that one picture crosses and the other doesn't. It is the same class as
     the composite's shoe false positives (changeover practice slides, 10/19), not a geometry error.
   - It would show on a member's practice card as a throw with no release.
3. **A pairing bug in the comparison script (fixed).** It paired throws by rest time first. t_2485.9 and
   t_2976.2 were released 0.01-0.02 s apart in both runs but last seen 1.2-1.8 s apart, so each counted as
   two unmatched throws.
   - It now pairs by release when both runs have one:
     `test_compare_pairs_one_release_whose_rests_were_seen_apart`.

## Reading

The per-view path reads the same throws, times and in-view rests as the composite, at the same latency,
from a calibration that agrees within 0.3%. Where they differ is where the tracker is already at its
weakest (stones leaving the panel, a player's shoes at the edge), and there the differences are of the kind
any change to the picture would make. Nothing points at the cuts, the clock or the calibration route.

Whether that is good enough to deploy is the user's call. The gate's rest rule did not foresee stones that
leave the view.
