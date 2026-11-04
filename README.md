# TatkalMate AI

A Windows desktop assistant (PySide6) that **prepares** you for an IRCTC Tatkal booking and **alerts** you at the right moment. You still log in, solve the CAPTCHA, book and pay on IRCTC yourself, in Brave.

![Dashboard](docs/screenshots/01-dashboard.png)

## What it does

| Area | What you get |
|---|---|
| Dashboard | Journey, millisecond countdown, system status, latest availability with its freshness, readiness checklist |
| Journey | Train, stations, date, class, quota, and a "boarding day" offset for mid-route boarding |
| Tatkal scheduler | AC 10:00 IST, non-AC 11:00 IST, one day before the train leaves its **origin** station. Survives restarts and system clock changes |
| Passengers | Up to 4 per Tatkal booking, validated against IRCTC field limits, names encrypted at rest (Windows DPAPI) |
| Preparation | Readiness checklist that labels every item as *from app data*, *auto-detected* or *confirmed by you*. eWallet balance reminder and payment walkthrough |
| Availability | Provider abstraction, rate-limited polling, backoff, timeouts, de-duplication, stale/cached detection, full history |
| Notifications | Windows toasts, in-app centre, sounds, duplicate suppression (persists across restarts) |
| Browser | Detects Brave, launches it with your normal profile on the IRCTC site, watches for unexpected closure, brings it to the front |
| Reliability | Single instance, crash reports, unclean-exit recovery, supervised background tasks, log rotation, health checks |

## What is NOT connected yet (read this)

1. **No real availability data.** IRCTC has no public API for individuals, and scraping its site or calling its undocumented endpoints isn't permitted. The app ships with:
   * `MockAvailabilityProvider`: **simulated** data. Every simulated result is labelled "Simulated" in the UI and in notifications, and is never shown in green.
   * `AuthorizedAvailabilityProvider`: an adapter for a **licensed** provider (an IRCTC-authorised partner API or a commercial feed whose terms allow this). Fill in `.env` from `.env.example`. The JSON contract is documented in `providers/authorized_provider.py`. If your provider's API looks different, subclass it and override `_build_request` and `_parse`. It refuses `irctc.co.in` / `indianrail.gov.in` hosts and plain HTTP.
2. **IRCTC login status cannot be detected.** TatkalMate does not read the browser. "Logged in" is only ever *confirmed by you*. It expires after 2 hours and resets if Brave closes.
3. **Wallet balance cannot be read.** You check it on IRCTC and tick it; the tick expires after 12 hours.

## Compliance boundaries (enforced in code)

* No automated form filling, booking submission, CAPTCHA solving or payment authorisation.
* Brave is started as `brave.exe <https url>`: no `--remote-debugging-port`, no `--user-data-dir`, no automation switches. Cookies, passwords, OTPs and tokens are never read or stored.
* Every provider request is at least **60 s** apart (`HARD_MIN_POLL_SECONDS`), or more if the provider asks. Manual "Check now" obeys the same gap. The 100 ms countdown makes no network calls.
* The network health probe connects to a neutral host (default `1.1.1.1:443`), never IRCTC.
* Passenger names are registered with the log redactor, and e-mails, phone numbers and OTP/password fragments are masked in every log line and crash report.

## Download (no Python needed)

Grab `TatkalMate-windows-x64.zip` from the [latest release](https://github.com/Razz0711/TatkalMate/releases/latest), unzip it anywhere (e.g. `C:\Apps\TatkalMate`) and run `TatkalMate.exe`.

The exe is **not code-signed**, so Windows SmartScreen shows "Windows protected your PC" on first launch. Click **More info → Run anyway**. Every build is produced by the public GitHub Actions workflow in `.github/workflows/windows-release.yml`, which also runs the test suite on Windows.

To publish a new version: bump `core/version.py`, then `git tag v0.1.1 && git push --tags`.

## Run from source on Windows 11

Requires Python 3.11 or newer (python.org installer, tick "Add to PATH").

```powershell
git clone <this repo> TatkalMate
cd TatkalMate
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Or double-click `scripts\run_windows.bat`, which sets up the environment the first time.

To build `TatkalMate.exe` locally: `pip install -r requirements-dev.txt` then `.\scripts\build_windows.ps1` (output in `dist\TatkalMate`).

Data lives in `%LOCALAPPDATA%\TatkalMate` (config, SQLite DB, `logs\`, `crashes\`). `python main.py --data-dir D:\somewhere` or `TATKALMATE_HOME` overrides it.

Keyboard: `Ctrl+1`…`Ctrl+9` switch pages.

## Suggested workflow on booking day

1. Day before: save the journey and passengers; set an estimated fare.
2. ~30 min before opening: **Browser → Launch Brave and open IRCTC**, log in yourself, then **Preparation → Confirm** the login.
3. Check the eWallet balance on IRCTC and tick it.
4. Reminders fire at 30/10/2 minutes (configurable) and at opening. Use **Bring Brave to front** and book.

## Architecture

```
main.py                      entry point: logging, single instance, crash handlers, Qt app
core/                        UI-independent foundations
  scheduler.py               Tatkal rules, countdown, clock-jump detection (pure)
  async_runtime.py           asyncio loop on a worker thread with supervised tasks
  event_bus.py               thread-safe pub/sub
  config.py                  JSON config, atomic writes, validation, compliance floor
  logging_config.py          JSON logs, rotation, PII redaction
  recovery.py                unclean-exit detection, crash reports
  secure_codec.py            DPAPI encryption for personal fields
models/                      journey, passenger, availability, notification, readiness
database/                    SQLite (WAL) + migrations + repositories
providers/                   base interface, mock, authorized adapter
services/                    brave, availability, notifications, alerts, passengers,
                             journey, wallet, readiness, reminders, health, container
app/                         PySide6 UI: main window, bridge to the GUI thread, views, theme
tests/                       85 tests (scheduler, monitor, providers, services, UI smoke)
```

Background work (monitoring, reminders, browser watch, health checks) runs on an asyncio loop in a worker thread. Services publish events on the bus; `app/bridge.py` re-emits them on the Qt thread, so services never touch widgets. `services/alert_service.py` is the single place that turns events into user-facing alerts, which keeps the "never claim unconfirmed availability" rule in one auditable spot.

### Availability freshness

| Label | Meaning |
|---|---|
| Verified | Source confirmed it, not cached, within the stale window, status known |
| Cached, not verified | Source said it came from a cache |
| Unconfirmed | Source did not vouch for it, or status unknown |
| Stale | Older than "Mark data stale after" (default 15 min) |

"Seats available" alerts are only raised for **Verified** results; everything else produces an "Unverified change" warning.

## Tests

```bash
pip install -r requirements-dev.txt
pytest                       # 85 tests; UI tests run offscreen
QT_QPA_PLATFORM=offscreen python main.py --smoke-test 5
QT_QPA_PLATFORM=offscreen python scripts/screenshots.py screenshots
```

Windows-only code (DPAPI, toasts, `winsound`, window focusing, registry lookup) is isolated behind injectable functions and degrades to no-ops elsewhere. The test suite runs on Linux; those paths still need a manual check on a real Windows 11 machine (see "Verify on Windows" below).

## Verify on Windows (not yet done)

The app was built and tested on Linux. On first run on Windows 11, please check:

- [ ] Brave is detected (Browser page shows the path and "Found via").
- [ ] "Launch Brave and open IRCTC" opens a tab in your existing Brave window and profile.
- [ ] "Bring Brave to front" works when Brave is minimised.
- [ ] Notifications → "Send test notification" shows a Windows toast and plays a sound.
- [ ] Diagnostics → "Personal data protection" says *Windows DPAPI encryption*.
- [ ] Launching a second copy just brings the first window forward.

## Tatkal rule references

Rules as implemented (`core/scheduler.py`): booking opens one day before the journey, excluding the journey date, counted from the train's originating-station departure date; AC classes at 10:00 and non-AC at 11:00 IST; no Tatkal in 1A; max 4 passengers per PNR. IRCTC changes rules from time to time, so re-check them before relying on the countdown.
