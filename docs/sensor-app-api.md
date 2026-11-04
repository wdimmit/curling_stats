# Sending sensor timings to curling.dimmit.net

curling.dimmit.net watches practice sessions through the club's cameras. A player can also have
their sensor app send each throw's times:

- **short:** back line to the throwing hog line;
- **long:** hog line to hog line.

The site matches each timing to the throw its cameras recorded and shows the times next to that
throw on the player's practice page.

## 1. Getting a key

The player makes the key, not you:

1. Sign in at https://curling.dimmit.net/practice
2. Open **Sensor app keys**, give the key a name and tap **Make a key**.
3. Copy the key into your app. It starts with `csk_` and is **shown only once**.

A key sends timings for the player who made it. It can be revoked on the same page, after which
it gets `401`. The key works only for the endpoint below, so don't use it for anything else.

## 2. Sending timings

```
POST https://curling.dimmit.net/api/practice/sensors
Authorization: Bearer csk_...
Content-Type: application/json

{"sent_at": 1793023149870,
 "timings": [
  {"client_id": "a1b2c3-0001", "at": 1793023145123, "at_is": "hog",
   "short_s": 3.21, "long_s": 13.94}
]}
```

`sent_at` goes once on the request, beside `timings`.

| Field | Type | Meaning |
|---|---|---|
| `sent_at` | number | The phone's clock, in epoch **milliseconds**, as you send the request. Read it fresh for every request, including retries. Optional, but send it: see below. |
| `client_id` | string, 1–64 of `A–Z a–z 0–9 . _ : -` | Your id for this throw, unique per player. If you send the same id twice, the site keeps only the first, so it is safe to retry. |
| `at` | number | When the rock crossed **one** line, in epoch **milliseconds**, by the phone's clock. Use the crossing time itself, not the time you send. |
| `at_is` | `"back"`, `"hog"` or `"far"` | Which crossing `at` is: the back line, the throwing hog line, or the far hog line. |
| `short_s` | number or null, 1.5–8 | Seconds from the back line to the hog line. |
| `long_s` | number or null, 6–30 | Seconds from hog line to hog line. |

Rules for the fields:
- Send at least one of `short_s` and `long_s`.
- `at_is: "back"` needs `short_s`.
- `at_is: "far"` needs `long_s`.
- `sent_at` must be within a day of the site's clock. Otherwise the whole request gets a `422`, because it is almost certainly in seconds instead of milliseconds.
- Send 1–50 timings per request. One per throw, as it happens, is ideal. If the app was offline,
  send the backlog in one go.

## 3. What comes back

The response has one entry per timing, in the order sent:

```json
{"timings": [
  {"client_id": "a1b2c3-0001", "id": "st_…", "session_id": "ps_…"},
  {"client_id": "a1b2c3-0002", "error": "at_is far needs long_s"}
]}
```

| Status | Meaning |
|---|---|
| `200` | Each entry has an `id`, meaning it was kept, or an `error`. A timing with an error was not kept, and sending it again unchanged won't help. |
| `409` | None of the timings had a session to go into, because the player had no practice session running then. The body has the same shape. Keep the timings and retry later only if the player may still start one. |
| `401` | The key is missing, unknown or revoked. |
| `422` | The body isn't `{"timings": [...]}` with 1–50 entries, or `sent_at` isn't epoch milliseconds. |
| `429` | Too many requests: over 300 an hour or 1,500 a day. Back off and retry. |
| `404` | Practice is switched off on the site. |

On a network error or a `5xx`, retry with the same `client_id`s.

## 4. Good to know

- **The player starts a session on the site before throwing.** A timing goes into the session that
  was running when the rock crossed the hog line. That window runs from 5 s before Start to
  2 minutes after the session ended, so late sends still count.
- **Send `sent_at` so the phone's clock doesn't matter.** The site matches a timing to the throw
  that crossed the hog line within 4 s of it, so the times have to be on the site's clock.
  Phone clocks can be off by much more than that: in the first real session a phone ran 44 s fast,
  and almost nothing matched. With `sent_at`, the site measures how far the phone's clock is off
  and corrects every `at` in the request. The only error left is how long the request takes to
  arrive, usually well under a second.
- Without `sent_at`, the site takes `at` as it is, so the phone must be on network time.
  A timing more than 60 s ahead of the site's clock is refused.
- The cameras publish a throw about 15–20 s after the rock stops. A timing sent before then
  matches as soon as the throw appears, so you don't need to wait before sending.

## Try it

```sh
KEY=csk_...
curl -sS https://curling.dimmit.net/api/practice/sensors \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "{\"sent_at\":$(date +%s%3N),\"timings\":[{\"client_id\":\"test-1\",\"at\":$(date +%s%3N),\"at_is\":\"far\",\"short_s\":3.2,\"long_s\":13.9}]}"
```

If no session is running, this returns `409`. Start one on the site first.
