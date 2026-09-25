# Restoring TeslaMate from a backup

A backup nobody has restored is a guess. Do the drill below **once, now**, while
nothing is wrong — not the day the NUC dies.

## The drill (safe — it never touches the live database)

Run from the Advanced SSH & Web Terminal add-on on the Home Assistant OS host.

```bash
DB=$(docker ps --format '{{.Names}}\t{{.Image}}' | grep -iE 'postgres|timescale' | head -1 | cut -f1)
DUMP=$(ls -1t /share/teslamate/backup/teslamate-*.dump | head -1)
export PGPASSWORD=$(tr -d '\r\n' < /share/teslamate/ops/.pgpass-teslamate)
```

Create a scratch database, restore into it, and count what arrived:

```bash
docker exec -e PGPASSWORD -i "$DB" psql -U postgres -c 'DROP DATABASE IF EXISTS teslamate_scratch'
docker exec -e PGPASSWORD -i "$DB" psql -U postgres -c 'CREATE DATABASE teslamate_scratch'
docker exec -e PGPASSWORD -i "$DB" pg_restore -U postgres -d teslamate_scratch --no-owner < "$DUMP"
docker exec -e PGPASSWORD -i "$DB" psql -U postgres -d teslamate_scratch -c \
  "SELECT 'cars' t, count(*) FROM cars UNION ALL
   SELECT 'drives', count(*) FROM drives UNION ALL
   SELECT 'charging_processes', count(*) FROM charging_processes UNION ALL
   SELECT 'positions', count(*) FROM positions"
```

Those counts must match the live database. Compare against the same query run with
`-d teslamate`. **If they don't match, the backup is not doing its job** and that is
worth finding out today.

Then point Grafana at the scratch database for five minutes and open the Drives
dashboard. Restoring rows is necessary; rendering them is the actual test.

Clean up:

```bash
docker exec -e PGPASSWORD -i "$DB" psql -U postgres -c 'DROP DATABASE teslamate_scratch'
```

## Real recovery, after losing the host

1. Rebuild Home Assistant OS and restore its own backup, which brings back the add-ons
   and their configuration.
2. Start the PostgreSQL add-on. Let the TeslaMate add-on start **once** so its
   migrations create an empty, current-schema `teslamate` database, then stop it.
3. Restore over the top:
   ```bash
   docker exec -e PGPASSWORD -i "$DB" pg_restore -U postgres -d teslamate \
     --clean --if-exists --no-owner < teslamate-YYYYMMDD.dump
   ```
4. Start TeslaMate. It will run any migrations newer than the dump.
5. Start the bridge add-on.

### Two things that will bite

- **Restore into a database whose schema TeslaMate created**, not into a blank one you
  made yourself. The dump carries `--no-owner`, so roles do not have to match, but the
  extensions and the `private` schema do.
- **Do not change the bridge's `vin` option to recover history.** The VIN is how
  TeslaMate identifies the car; a different one registers a *new* car and orphans every
  drive and charge already recorded. If you have restored a dump, the VIN in the add-on
  config must be the same one that produced it.

## Where the copies live

| | |
|---|---|
| Primary | `/share/teslamate/backup/` on the HA host, 14 daily dumps |
| Offsite | whatever the Google Drive Backup add-on carries, since it includes `/share` |
| Optional third | set `MIRROR=` in the backup script to a mounted NAS path |

The dumps are small — TeslaMate's bulk is the `positions` table at roughly 95 k rows a
year, which compresses to a few MB. Fourteen of them is not a storage decision.
