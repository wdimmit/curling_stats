# Design records

These are dated design specs (`specs/`) and implementation plans (`plans/`),
written before each feature was built. They are not kept up to date: where one
disagrees with a README or the code, the README and the code win.

| feature | spec | plan | status |
| --- | --- | --- | --- |
| Phone viewer | 2026-09-11-phone-viewer-design | 2026-09-11-phone-viewer | shipped 2026-09-14 (150e658c); the plan's no-build-step frontend superseded by React |
| Long-camera hog crossing | 2026-09-14-long-camera-hog-crossing-design | 2026-09-14-long-camera-hog-crossing | shipped (dd74b57f) |
| ds13 side-view frames | 2026-09-15-side-view-frame-extraction-design | 2026-09-15-side-view-frame-extraction, 2026-09-15-shot-driven-frames | shipped, ds13a (8cf568e2) to ds13e; see `datasets/ds13/README.md` |
| Scoreboard card OCR | 2026-09-17-scoreboard-ocr-design | 2026-09-17-scoreboard-ocr | shipped (54de6a04, 5db7743c) |
| Throw stage 1, the release line | 2026-09-19-throw-stage-1-design | 2026-09-19-throw-stage-1 | shipped (db182cf4) |
| Far hog line from the paint | 2026-09-22-phase3-far-tripwire-design | 2026-09-22-phase3-far-tripwire | shipped (e207cbb1) |
| Target broom | 2026-09-23-target-broom-design | 2026-09-23-target-broom-phases-1-2, -3-5 | shipped (broom1 7dbfafa7; now broom4) |
| Shot detail on the desktop | 2026-09-24-desktop-shot-detail-design | 2026-09-24-desktop-shot-detail | shipped (f24bca0f) |
| Shot line | 2026-09-24-shot-line-detail-design | 2026-09-24-shot-line-pipeline, -viewer | shipped (4d88fa58) |
| Flag an issue | 2026-09-25-flag-an-issue-design | 2026-09-25-flag-an-issue | shipped (047eecf5) |
| Mixed doubles | 2026-09-25-mixed-doubles-design, -phase0 | 2026-09-25-doubles-*, 2026-09-26-doubles-* | shipped 2026-09-26 (fc4d2a22) |
| Game report, entered scores | 2026-10-01-game-report-and-entered-scores-design | 2026-10-01-game-report-and-entered-scores | shipped (c9ab2ce8) |
| Split sync correction | 2026-10-02-split-sync-correction-design | 2026-10-02-split-sync-correction | parked, not built |
| I played… and My shots | 2026-10-04-my-shots-design | — | shipped (78be8404, 16abd6ae) |
| Nightly review, a week of live recordings | 2026-10-05-nightly-review-design | 2026-10-05-nightly-review, 2026-10-05-live-recordings-week | shipped (bcb21d99, 3c2c7eb4); recordings now kept 21 days |
| Practice sessions | 2026-10-09-practice-sessions-design | 2026-10-09-practice-phase1-*, 2026-10-10-practice-phase2-worker, -phase3-4-api-page | shipped 2026-10-10 (a93ab936); its video source superseded by practice direct feeds |
| Game clips | 2026-10-10-game-clips-design | 2026-10-10-game-clips | shipped (1adf9080) |
| Practice clips | 2026-10-10-practice-clips-design | 2026-10-10-practice-clips | shipped 2026-10-10 |
| Release spin | 2026-10-10-release-spin-design | 2026-10-10-release-spin | shipped (spin1 1c70ad67, 710b07cf) |
| Short-handed teams | 2026-10-14-short-handed-teams-design | 2026-10-14-short-handed-teams | shipped (7c37a0e6) |
| Charting at 1080p | 2026-10-15-charting-at-1080p-design | 2026-10-15-charting-at-1080p | shipped (75bec307) |
| Chart write protection | 2026-10-15-chart-write-protection-design | 2026-10-15-chart-write-protection | shipped (c9815c64) |
| Charting on an iPad | 2026-10-16-charting-on-an-ipad-design | 2026-10-16-charting-on-an-ipad | shipped (c48838f0) |
| Doubles switched ends | 2026-10-21-doubles-switched-ends-design | 2026-10-21-doubles-switched-ends | shipped (2b775090) |
| Emailed-link sign-in | 2026-10-21-email-link-sign-in-design | 2026-10-21-email-link-sign-in | shipped (342c37a6, on in 876a5bd2) |
| False-positive frame harvest | 2026-10-21-fp-frame-harvest-design | 2026-10-21-fp-frame-harvest | shipped (833881de) |
| Member labelling | 2026-10-22-member-labelling-design | 2026-10-22-member-labelling | shipped (7abbff1e) |
| Release point | 2026-10-22-release-point-design | 2026-10-22-release-point | shipped (32222284) |
| Practice on the club's cameras | 2026-10-24-practice-direct-feeds-design | 2026-10-24-practice-direct-feeds (+ .results) | shipped 2026-10-25 (df9f6c13) |

Names are file names without `.md`. Don't rename or move these files: code and
the READMEs cite their paths.
