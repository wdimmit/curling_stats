# Sensor timings in practice sessions

*2026-10-25. Plan: `~/.claude/plans/i-have-an-app-linked-forest.md`.*

## What it is for

A player brings their own timing sensors. The app that reads them knows two times for each
throw:

- **short:** from the back line to the throwing hog line;
- **long:** from hog line to hog line.

The app pushes these into the player's practice session. When the session is read, the site pairs
each one with the throw the cameras recorded. The pairs are also kept, so the cameras' split can be
checked against the sensors (`scripts/practice/sensor_pairs.py`).

The user's decisions:

- **Whose kit:** the player's own. A timing goes into that person's session, never into someone
  else's, even on the same sheet.
- **Auth:** an API key the player makes on the site. It is not a Firebase sign-in.
- **What the app knows:** both durations, plus the wall-clock time of one of the three line
  crossings.
- **The page:** it shows the sensor times beside the cameras' and lists timings no throw paired with.
  It shows none of this on a session no app pushed to.

## For the app: the API

### Getting a key

The player makes a key on **My practice** (`/practice`), under "Sensor app keys", and pastes it into
the app.

- It starts `csk_`.
- It is shown once. The site keeps only its hash.
- Revoking it there stops it at once.
- It works only for pushing timings. It is not a sign-in for anything else.

### Pushing timings

```
POST https://curling.dimmit.net/api/practice/sensors
Authorization: Bearer csk_...
Content-Type: application/json

{"sent_at": 1793023149870, "timings": [
  {"client_id": "9f1c0e2a-0001", "at": 1793023145123, "at_is": "hog",
   "short_s": 3.21, "long_s": 13.94}
]}
```

| field | meaning |
|---|---|
| `sent_at` | Optional, once per push: epoch ms by the phone's clock as it sends. The site takes `clock_offset_s` = its own clock − `sent_at` and adds it to every hog time in the push, so the phone's clock no longer matters. Not within a day of the site's clock → 422. Added 2026-10-27, after the first real session's phone ran 44 s fast. |
| `client_id` | The app's own id for this throw: 1-64 of `A-Z a-z 0-9 . _ : -`. Sending the same one again is a no-op, so retry anything you are unsure arrived. |
| `at` | Epoch **milliseconds** of one line crossing, by the phone's clock. |
| `at_is` | Which crossing `at` is: `back`, `hog` (the throwing hog line) or `far` (the far hog line). |
| `short_s` | Seconds from back line to hog line, 1.5-8. May be null. |
| `long_s` | Seconds from hog line to hog line, 6-30. May be null. |

At least one duration is required.
- `at_is: "back"` needs `short_s`.
- `at_is: "far"` needs `long_s`.

The site works out when the rock crossed the throwing hog line, and pairs on that time.

You can send 1-50 timings per push, so an app that was out of signal can catch up in one go.

### Which session a timing goes into

A timing goes into the player's session that was running when the rock crossed the hog line. That
window runs from 5 s before Start to 2 minutes after the session ended. A timing whose throw was
**before** the session was started never goes in, so start the session on the site before throwing.

A timing more than 60 s ahead of the site's clock is refused, because the phone's clock is wrong.
Without `sent_at`, keep the phone on network time.

### The answer

The response is 200, with one entry per timing, in order:

```json
{"timings": [
  {"client_id": "9f1c0e2a-0001", "id": "st_...", "session_id": "ps_..."},
  {"client_id": "9f1c0e2a-0002", "error": "at_is far needs long_s"}
]}
```

| status | when |
|---|---|
| 200 | Each entry has an `id` (kept) or an `error` (not kept, and retrying unchanged won't help). |
| 409 | No timing in the push had a session to go into: no practice session of the player's was running. Same body. |
| 401 | The key is missing, unknown or revoked. |
| 422 | The body isn't `{"timings": [...]}` with 1-50 entries. |
| 429 | More than 300 pushes in an hour or 1,500 in a day. |
| 404 | Practice is switched off on the site. |

### Example

```sh
curl -sS https://curling.dimmit.net/api/practice/sensors \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "{\"timings\":[{\"client_id\":\"t1\",\"at\":$(date +%s%3N),\"at_is\":\"far\",\"short_s\":3.2,\"long_s\":13.9}]}"
```

## On the site

### Storage

- `SensorTiming` records go in `practice_timings`. The id is `st_` plus a hash of the person's id
  and the `client_id`.
- `ApiKey` records go in `api_keys`.
- Both are backed up with everything else.

### Pairing

Pairing happens in `service/practice.match_timings`, at read time, as hidden throws do.

- **Which throws are candidates:** the throws in the session's window. Placed stones are left out.
  Hidden throws are kept, so a timing never shows as unpaired because its throw was hidden.
- **The camera's time for a throw:** it crosses the hog line at `t_hog_s`. If that is missing, the
  site uses `t_far_s` less the timing's `long_s`.
- **How the pairs are chosen:** the closest pairs go first, one throw to one timing, within 4 s.
  One person's throws are 20 s or more apart.
- **A timing that arrives first:** it can come before the cameras publish its throw, which takes
  about 15-20 s. It pairs on the next read. Until it is a minute old in a running session, the page
  calls it "still measuring" rather than "the cameras saw no throw".

### The page

When `has_sensors` is true:

- the throws table gets a Sensor column with the long time;
- the throw card gets a Sensor line with both times;
- unpaired timings are listed in time order among the throws.

When `has_sensors` is false, the page is exactly as it was.

### Session clocks

Timings don't keep a session alive. The idle rule (15 minutes with no throw) still counts the
cameras' throws only.

### Validation

`GET /api/admin/practice/timings?session=ID` or `?user=UID` (admin token) returns each session's
pairs and unpaired timings. `scripts/practice/sensor_pairs.py` prints them, with:

- the median and p90 of camera split − sensor long;
- the median clock gap.
