# STL special-livery alerts

Free, serverless phone alerts when an aircraft with a special livery is at, arriving at, or leaving
St. Louis Lambert (STL). Runs every ~10 minutes on GitHub Actions, reads free ADS-B data from
[adsb.lol](https://www.adsb.lol/docs/open-data/api/), and pushes to your phone through [ntfy](https://ntfy.sh).

## How it works

1. One request to adsb.lol returns every aircraft within 120 nm of STL, each with its registration (tail number).
2. Each tail is looked up in `data/liveries.csv` (**all airlines, no airline filter**, so diversions from any carrier match).
3. Phase is classified from position/altitude/heading: `inbound` (descending toward STL, <=120 nm),
   `arrival`/`departure` (<=40 nm), `ground` (<=4 nm, on the surface). High-altitude overflights are ignored.
4. Duplicates are suppressed per tail+callsign for 6 h; muted tails are skipped.
5. Alert -> ntfy (titled `Live: ...`), with a **Mute 24h** button; tapping it opens FlightRadar24 for the tail.

`watch_airlines` in `config/config.json` only sets the "routine" vs "possible diversion" wording and sort order.
It never decides whether an alert fires.

## The schedule pass (AeroDataBox): planned alerts and swap detection

The ADS-B pass can only see a plane when it is airborne or transmitting near STL (roughly 30 min before an
arrival). The **schedule pass** covers the hours before that: it reads STL's board from AeroDataBox
(`GET /flights/airports/icao/KSTL`, "FIDS by relative time", TIER 2 = 2 API units per call), looks at the
tail number assigned to each flight, and checks it against the same livery database with the same rules
(no airline filter, same `alerts.statuses` / `alerts.kinds`).

* **No tail yet?** Skipped silently and re-checked on a later poll. Nothing is alerted until a tail appears.
* **Tail assigned and special:** a **Planned** alert with airline, flight number, direction, scheduled time,
  gate/terminal (when AeroDataBox has them) and livery. It says plainly that it is a plan, not a sighting.
* **Swap detection.** The tail last seen on each flight (per flight number + date + direction) is stored in
  `state/state.json`. If a later poll shows a different tail:
  * standard -> special: **Swapped IN**
  * special -> standard: **Swapped OUT**
  * special -> a different special: one combined **Livery changed** alert
  * unchanged tail: nothing, ever.
* **Planned and Live are different alerts and never block each other.** The schedule pass sends **Planned**
  (a tail was assigned; the plane may not even have left yet). Later, when the ADS-B pass actually sees that
  tail flying near STL, you get a separate **Live** alert, even if you were already sent the Planned one. Only true
  duplicates are suppressed: the same pass reporting the same flight twice (ADS-B: same tail + callsign within 6 h;
  schedule: an unchanged tail on the same flight). Muting a tail silences both passes.
* **Tap any alert to open FlightRadar24** for that tail (`https://www.flightradar24.com/data/aircraft/<TAIL>`),
  via ntfy's `Click` header. The Mute button is a separate action button.
* **If a send fails,** the flight's tracking is rolled back so the next poll retries it.
* **Independent passes.** Each pass is wrapped so an AeroDataBox error, rate limit or quota problem is logged and
  skipped (the API is then left alone for 2-3 hours) and the ADS-B pass carries on.

### Staying inside the free tier

Measured on the RapidAPI **Basic** plan: **400 API units/month** (and 1,600 requests/month). A board call is **2 units
regardless of its time window** (a 3 h window and a 12 h window both cost 2), so every poll asks for the full 12 h
and the only levers are how many polls to make and when.

The pass spends the whole month's budget evenly. Each day it works out
`floor(calls left / days left)` polls (at most 7, leaving a 20-unit reserve), and places them across the STL
operating day at the UTC hours in `schedule.poll_schedule_utc`, just ahead of the departure banks:

| Polls/day | Times (CDT) |
|---|---|
| 6 (a normal month) | 6a, 9a, 12p, 3p, 6p, 9p |
| 4 | 6a, 10a, 2p, 6p |
| 1 | 12p |

Frequency is deliberately higher in the daytime, because at STL tails show up mostly 0-3 hours before a flight,
so a flight's tail is most likely to appear between polls that are only a few hours apart. Flights days out
are not polled separately at all: they are simply not in the 12 h window.

**Extra polls** (up to 2/day, at most every 90 min) happen only while a flight already known to be special is
within 3 h of its time, to catch a late tail swap, and only out of budget left over after every remaining daily
poll is paid for. The ledger reads the API's own remaining-units header after every call, so anything spent
elsewhere (like a probe) is counted. If the API errors or rate-limits, the pass backs off for 2-3 hours and the
ADS-B pass is unaffected.

`state/lead_times.csv` records when each flight's tail first appeared; run `python scripts/lead_time_report.py`
after a few days to see how far ahead tails really get assigned at STL.

### Setup for the schedule pass

1. Get an AeroDataBox key. The free route (verified working) is **RapidAPI -> Basic plan**
   (400 units/month, "free forever"): open [rapid.aerodatabox.com](https://rapid.aerodatabox.com), sign in to
   RapidAPI, click **Subscribe** on the Basic plan, then copy the `X-RapidAPI-Key` from the code snippet.
   API.Market's Basic plan is only a 7-day trial, and AeroDataBox's own "direct" plans are paid (from $19/month)
   and were listed as "coming soon", so they are not a free option.
2. GitHub repo -> Settings -> Secrets and variables -> Actions: add secret **`ADB_KEY`**, and (Variables tab)
   **`ADB_PROVIDER`** = `rapidapi` (or `apimarket` / `direct`, matching where the key came from).
3. Test locally first (spends 2 units): `ADB_KEY=... ADB_PROVIDER=rapidapi python scripts/probe_aerodatabox.py`
   (PowerShell: `$env:ADB_KEY="..."; $env:ADB_PROVIDER="rapidapi"; py scripts/probe_aerodatabox.py`).
   It shows how many flights have a tail, whether gate/terminal are populated, and the rate-limit headers.
4. Offline test with no key: `python stl_alerts.py --dry-run --schedule-fixture some_fids.json`
   (`--force-schedule` really calls the API even in `--dry-run`).
5. `python stl_alerts.py --test-notify` sends one synthetic Live and one synthetic Planned alert (tail N492AS) so you can tap-test both.

## Setup

1. **Repo.** Create a *public* GitHub repo and push this folder (`git init`, add, commit, push).
2. **ntfy on your phone.** Install the ntfy app (iOS/Android), subscribe to a long random topic, e.g.
   `py -c "import secrets; print('stl-'+secrets.token_urlsafe(24))"`. Treat it like a password.
3. **GitHub secret.** Repo -> Settings -> Secrets and variables -> Actions -> New secret:
   `NTFY_TOPIC` = your topic. (You don't need to subscribe to `<topic>-ctl`; the Mute button posts to it silently.)
4. **Workflow permissions.** Settings -> Actions -> General -> Workflow permissions -> *Read and write*
   (the job commits `state/state.json`).
5. **Test the phone** (locally): `NTFY_TOPIC=<topic> python stl_alerts.py --test-notify`
   (PowerShell: `$env:NTFY_TOPIC="<topic>"; py stl_alerts.py --test-notify`). Tap **Mute 24h** to try the button;
   the next poll applies it.
6. Actions tab -> `poll-stl` -> **Run workflow** once to confirm it goes green. Cron takes over after that.

Try it without a phone: `python stl_alerts.py --dry-run`, or `--dry-run --fixture some.json` with an adsb.lol-shaped file.
Tests: `python -m unittest discover -s tests`.

On some Windows Python installs HTTPS fails with "certificate has expired". Point Python at a good CA bundle,
e.g. `SSL_CERT_FILE="C:/Program Files/Git/mingw64/etc/ssl/certs/ca-bundle.crt"`. GitHub's runner is unaffected.

## Muting

Tap **Mute 24h** on an alert (applies at the next poll, up to ~10 min). You can also mute from any ntfy client by
publishing to `<topic>-ctl`: `mute N8977G 72` or `unmute N8977G`.

## The livery database (`data/liveries.csv`)

One row per tail. Columns: `registration, icao24, airline, aircraft_type, livery_name, kind, description, status,
confidence, painted_date, as_of, sources, notes`.

* `status`: `active` | `unverified` (alerts, flagged in the message) | `retired` (never alerts).
* `confidence`: `verified` = cross-checked in 2+ sources or by you; `best-effort` = single/unchecked source.
* `kind`: `livery`, `retro`, `decal` (nose decal on a standard-paint plane), `partnership`, `other`.
  Change `alerts.kinds` in the config to drop kinds you don't care about.

**There is no authoritative master list.** This one is Southwest (parsed from Wikipedia, 5 tails cross-checked),
plus a hand-seeded best-effort set for American, Delta, Alaska, Frontier, British Airways, Lufthansa and KLM.
It is incomplete and will go stale. Conflicts between sources are recorded in `notes` and `data/conflicts.csv`.

**Add a tail in seconds:**

    python scripts/add_livery.py N8977G "Southwest Airlines" "Louisiana One" --type B38M --url https://...

or just edit the CSV. Rows added by hand are `verified` and are never overwritten by the refresh script.

**Bulk refresh:** `python scripts/refresh_liveries.py` re-pulls the Southwest table from Wikipedia, adds new tails,
updates unprotected rows, logs disagreements with protected rows to `data/conflicts.csv`, and deletes nothing.

## Upkeep you should expect

* **Every 1-2 months (10 min):** run the refresh script, skim `data/conflicts.csv`, commit.
* **When Southwest or others announce a new livery:** add the tail (1 min).
* **If GitHub emails that the scheduled workflow was disabled** (idle repos are paused after ~60 days): re-enable it.
  The job touches `state.json` monthly to reduce this, but I haven't confirmed bot commits count as activity.
* **AeroDataBox quota:** watch `state/state.json` -> `adb.units`; if your plan allows less than 400 units/month, lower `monthly_budget_units`.
* **If adsb.lol starts requiring a key:** add it as the `ADSB_API_KEY` secret.
* Coverage caveat: ADS-B is weak for aircraft parked on the ground with transponders off, so "on the ground" alerts are best-effort.
