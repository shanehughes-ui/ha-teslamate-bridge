# ha-teslamate-bridge

**Any car that lives in Home Assistant, presented to a stock TeslaMate as a Tesla.**

[![Tests](https://github.com/shanehughes-ui/ha-teslamate-bridge/actions/workflows/test.yml/badge.svg)](https://github.com/shanehughes-ui/ha-teslamate-bridge/actions/workflows/test.yml)

TeslaMate gives Tesla owners something no other EV logger does well: Grafana
dashboards, per-drive route maps, a real SQL history and long-horizon battery
health. It only speaks to Tesla's Owner API. This is a small, read-only service that
answers that API from Home Assistant state, so TeslaMate runs **unmodified** and never
learns the car is not a Tesla. Built for a Geely EX2; the entity mapping is
configuration.

```
car's cloud ──► Home Assistant ──► ha-teslamate-bridge ──Owner API──► TeslaMate (stock)
                (any integration)   (this, a local add-on)                  │
                                                                   PostgreSQL + Grafana
```

## Why a shim and not a fork

TeslaMate's maintainers have said non-Tesla sources are not on the roadmap, its
schema changes constantly (six core-table migrations in one year), and a rewrite is
announced. A fork would be orphaned. But `TESLA_API_HOST` and `TESLA_AUTH_HOST` are
first-class, documented configuration — it is how the third-party token providers
work — so one translator against a public contract keeps upgrades free.
[lucid-teslamate-bridge](https://github.com/epheterson/lucid-teslamate-bridge) proved
the approach for a Lucid; this does it for anything Home Assistant already knows.

## What it serves

Three GETs and a token POST. That is the whole contract.

| Route | What |
|---|---|
| `POST /oauth2/v3/token` | Accepted unconditionally. Auth between two containers that trust each other by construction would be theatre. |
| `GET /api/1/products` | The car, with `state: online` or `asleep`. |
| `GET /api/1/vehicles/{id}` | Same. |
| `GET /api/1/vehicles/{id}/vehicle_data` | The full payload — or **HTTP 408** when the car is parked and unplugged, which is how TeslaMate learns it is asleep. |
| `GET /health` | The operator's view: SoC, charging and gear as the bridge sees them, and why the last GPS fix was accepted or rejected. |

Every command-shaped route (`/command/*`, `/wake_up`) is refused with 405 and logged.
**There is no code path that can act on the car.** The only upstream call in the
program is a `GET` of `/api/states`.

## The judgement calls, and why

These are the parts TeslaMate cannot do for itself because it assumes Tesla-quality
telemetry. Each is a pure, tested function in `bridge/sanitise.py`.

- **Units.** The Owner API is miles and mph. TeslaMate converts back to km on ingest,
  so feeding it km inflates every distance by 1.609 and it looks plausible. Every
  distance goes through `units.py`, using TeslaMate's own conversion constant, with a
  golden test that 100 km reads as 62.137 miles.
- **Sleep.** A parked, unplugged car is reported asleep (408). Otherwise TeslaMate
  invents a ten-hour stationary drive. Driving or charging is awake; after either ends
  the bridge stays awake for a short grace period so TeslaMate sees the poll that closes
  the drive or charge cleanly. Data older than a threshold is asleep regardless.
- **Gear.** The car reports none. TeslaMate opens a drive on `shift_state` in D/N/R, so
  it is synthesised from the engine state: running → `D`, off → `P`, unknown → absent.
- **Outlier rejection.** The source position occasionally lags and then catches up —
  the worst measured hop was 7.2 km in one 31 s sample, an implied 839 km/h. Any fix
  implying more than 150 km/h since the last accepted one is held. Without this the
  route gets a spike and `speed_max` is nonsense forever.
- **Freshness.** A fix only counts when its timestamp advances. The upstream GPS once
  froze for weeks and resumed with no warning; a re-published old fix is not a new
  position.
- **Heading** is derived from consecutive accepted fixes (the car sends none).
- **`gps_accuracy` is ignored on purpose.** The source integration never implements it,
  so Home Assistant publishes its base-class default of 0 on every fix. The field
  carries no information.
- **Absent means absent.** A signal the car does not carry is `null`, never `0`. The
  car reports no outside temperature; you may name a local weather entity in
  `outside_temp_entity` and it is used, otherwise the field is null. The bridge never
  fabricates a number. There is no software version either, so `car_version` is a
  constant and TeslaMate's `updates` table stays empty — that is correct.
- **Charge energy is read, not recomputed.** `charge_energy_added` comes from the
  session-energy sensor of whatever already tracks charging in your Home Assistant (by
  default the [EV Stats](https://github.com/shanehughes-ui/ha-ev-stats-integration)
  integration). Two systems computing the same number will disagree.

## Install

The bridge is a **local add-on**. It needs no repository to appear in the store.

1. **Databases and dashboards** (Supervisor clicks, not scriptable): install the
   PostgreSQL 17 add-on (TeslaMate needs ≥ 16.7 / ≥ 17.3), the Grafana add-on
   (`a0d7b954-grafana`), and
   [`lildude/ha-addon-teslamate`](https://github.com/lildude/ha-addon-teslamate). Do
   not start TeslaMate yet.
2. **Put this add-on on the host.** From a clone on any machine with SSH to the HA OS
   box:
   ```
   ./deploy.sh
   ```
   It assembles `addon/` and `bridge/` into `/addons/ha-teslamate-bridge`. Then in
   Home Assistant: *Settings → Add-ons → Add-on Store → ⋮ → Check for updates → Local
   add-ons → HA TeslaMate Bridge → Install → Start.* Check its log and `/health`.
3. **Point TeslaMate at it.** In the TeslaMate add-on configuration:
   ```yaml
   env_vars:
     - name: TESLA_API_HOST
       value: http://local-ha-teslamate-bridge:8080
     - name: TESLA_AUTH_HOST
       value: http://local-ha-teslamate-bridge:8080
     - name: POLLING_DRIVING_INTERVAL
       value: "30"
   ```
   `env_vars` are applied last and override everything, so no fork of that add-on is
   needed. Then in TeslaMate's own settings for the car set **Use streaming API: off**
   — the underlying data refreshes every ~30 s, so streaming buys nothing and the
   polling interval above matches the source instead of writing a dozen duplicate rows
   per real sample.

The add-on reaches Home Assistant through the Supervisor (`homeassistant_api: true`), so
**no long-lived token is stored anywhere**. `HA_URL` / `HA_TOKEN` exist only for
running it outside Home Assistant during development.

## Options

| Option | Default | Meaning |
|---|---|---|
| `display_name` | `Geely EX2` | What TeslaMate calls the car. |
| `vin` | a synthetic 17-char string | TeslaMate requires one and keys the car on it. **Set once; changing it registers a new car.** |
| `car_entity_prefix` | `geely_e22h_gp_0137` | Every car entity is `sensor.<prefix>_battery` and so on. |
| `session_energy_entity` | `sensor.ev_stats_session_energy` | Where charge-session kWh is read from. |
| `outside_temp_entity` | *(empty)* | Optional local weather entity for `outside_temp`. |
| `max_plausible_kmh` | `150` | Fix-to-fix implied speed above which a fix is rejected. |
| `stale_after_seconds` | `2700` | Data older than this is asleep regardless. |
| `online_grace_seconds` | `300` | How long to stay awake after a drive or charge ends. |
| `entity_overrides` | `[]` | `{role, entity_id}` pairs to override any single role (see `ROLE_TEMPLATES` in `bridge/config.py`). |

## Verifying it

- **Units** — drive a known route; TeslaMate's distance must match the odometer delta.
  A 1.609× error means km leaked into a miles field.
- **Route** — no segment implying more than 150 km/h, and the polyline follows roads.
- **Segmentation** — TeslaMate's drive and charge counts should match whatever else
  segments the same car. They are independent state machines; disagreement is worth
  knowing either way.
- **Sleep** — a night parked produces `asleep`, not a ten-hour drive.
- **Restart** — restart the add-ons; no duplicate or orphaned open drives.

## Layout

```
bridge/units.py     km→mi, km/h→mph, psi→bar                       pure
bridge/sanitise.py  outliers, freshness, heading, gear, sleep      pure
bridge/mapping.py   HA snapshot → Owner API JSON                   pure
bridge/config.py    entity roles from add-on options               file + env only
bridge/hass.py      the one module that talks to Home Assistant    I/O
bridge/app.py       the four endpoints                              wiring
addon/              the add-on manifest, Dockerfile and entrypoint
tests/              pure tests; no Home Assistant, no network
```

CI runs the pure tests, asserts the pure modules import nothing from Home Assistant, the
web layer or the network, validates the add-on manifest, and **fails the build if
anything coordinate-shaped or identifying is committed**. Positions are the one thing
this project handles that must never end up in a repository.

## Not in scope

Cost attribution, tariffs, solar, CO₂ — the stats system that already does those keeps
doing them. Writing into TeslaMate's Postgres directly (its schema is explicitly not an
interface). Sending anything to the car, ever.

## License

MIT.
