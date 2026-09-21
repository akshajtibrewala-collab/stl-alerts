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
5. Alert -> ntfy, with a **Mute 24h** button.

`watch_airlines` in `config/config.json` only sets the "routine" vs "possible diversion" wording and sort order.
It never decides whether an alert fires.

**Not built yet:** the schedule-confirmation pass (AeroDataBox) for scheduled time and gate. The alert
message already has empty fields for it.

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
* **If adsb.lol starts requiring a key:** add it as the `ADSB_API_KEY` secret.
* Coverage caveat: ADS-B is weak for aircraft parked on the ground with transponders off, so "on the ground" alerts are best-effort.
