#!/usr/bin/env bash
#
# Back up the TeslaMate PostgreSQL database on a Home Assistant OS host.
#
# WHY THIS EXISTS AT ALL, RATHER THAN RELYING ON HOME ASSISTANT'S OWN BACKUP:
# a Home Assistant backup copies the PostgreSQL add-on's data directory as files,
# while the server is running. Postgres does not promise that a hot file copy is
# restorable -- pages can be captured mid-write and the WAL may not agree with the
# heap. It will usually work, and the time it does not will be the time you need it.
# pg_dump asks the server for a consistent snapshot instead, so what lands on disk
# is a database, not a photograph of one.
#
# WHERE IT RUNS: on the HA OS host, from the Advanced SSH & Web Terminal add-on,
# which has root and a Docker socket. The HA Core container has neither pg_dump nor
# docker, so a `shell_command:` automation cannot do this.
#
# INSTALL (once), in the SSH add-on's configuration:
#   init_commands:
#     - cp /share/teslamate/ops/backup-teslamate.sh /usr/local/bin/ && chmod +x /usr/local/bin/backup-teslamate.sh
#     - crontab -l 2>/dev/null | grep -q backup-teslamate || (crontab -l 2>/dev/null; echo "17 3 * * * /usr/local/bin/backup-teslamate.sh >> /share/teslamate/backup/backup.log 2>&1") | crontab -
#   Then restart the add-on. 03:17 rather than 03:00 so it does not collide with
#   every other cron job in the house.
#
# RESTORE (test this once, before you need it -- see ops/RESTORE.md):
#   pg_restore -h <host> -U <user> -d teslamate_scratch --clean --if-exists <dump>

set -euo pipefail

# ---------------------------------------------------------------- settings --
DB_NAME="${DB_NAME:-teslamate}"
DB_USER="${DB_USER:-postgres}"
DB_PORT="${DB_PORT:-5432}"
# Leave empty to auto-discover the PostgreSQL add-on container.
DB_HOST="${DB_HOST:-}"
# Read from the environment or a file so the password is never written here.
# e.g. PGPASSWORD_FILE=/share/teslamate/ops/.pgpass-teslamate  (chmod 600)
PGPASSWORD_FILE="${PGPASSWORD_FILE:-/share/teslamate/ops/.pgpass-teslamate}"

DEST="${DEST:-/share/teslamate/backup}"
KEEP="${KEEP:-14}"

# Optional second copy. Set to a mounted path; leave empty to skip. Deliberately
# not hardcoded to a particular NAS address -- set it per install.
MIRROR="${MIRROR:-}"

# ------------------------------------------------------------------- setup --
log() { printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die() { log "FAILED: $*"; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker not found -- run this from the SSH add-on on the HA OS host"

if [[ -z "$DB_HOST" ]]; then
  # The add-on slug varies by repository (alexbelgium, expaso/TimescaleDB, ...),
  # so match on the image rather than guessing the container name.
  DB_HOST="$(docker ps --format '{{.Names}}\t{{.Image}}' \
             | grep -iE 'postgres|timescale' | head -1 | cut -f1 || true)"
  [[ -n "$DB_HOST" ]] || die "no running PostgreSQL/TimescaleDB container found"
  log "discovered database container: $DB_HOST"
fi

if [[ -z "${PGPASSWORD:-}" && -r "$PGPASSWORD_FILE" ]]; then
  PGPASSWORD="$(tr -d '\r\n' < "$PGPASSWORD_FILE")"
fi
[[ -n "${PGPASSWORD:-}" ]] || die "no password: set PGPASSWORD or create $PGPASSWORD_FILE (chmod 600)"
export PGPASSWORD

mkdir -p "$DEST"
STAMP="$(date -u '+%Y%m%d')"
OUT="$DEST/teslamate-$STAMP.dump"
TMP="$OUT.partial"

# --------------------------------------------------------------- the dump --
# Run pg_dump INSIDE the database container: the add-on ships a client whose
# version matches the server, and a newer server refuses an older client.
log "dumping $DB_NAME from $DB_HOST"
if ! docker exec -e PGPASSWORD -i "$DB_HOST" \
      pg_dump -h 127.0.0.1 -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -Fc --no-owner \
      > "$TMP" 2>/tmp/pg_dump.err; then
  log "pg_dump stderr: $(head -5 /tmp/pg_dump.err 2>/dev/null || true)"
  rm -f "$TMP"
  die "pg_dump returned non-zero"
fi

# ------------------------------------------------------ verify BEFORE prune --
# The classic backup bug is rotating good dumps out on the strength of a new one
# that is empty or truncated. Nothing below deletes anything until the new file
# has been proven to be a readable archive containing the tables that matter.
SIZE="$(wc -c < "$TMP" | tr -d ' ')"
[[ "$SIZE" -gt 4096 ]] || { rm -f "$TMP"; die "dump is only ${SIZE} bytes"; }

# pg_restore --list parses the archive header and table of contents. It does not
# restore anything, and it fails loudly on a truncated or corrupt file.
TOC="$(docker exec -i "$DB_HOST" pg_restore --list < "$TMP" 2>/tmp/pg_restore.err || true)"
[[ -n "$TOC" ]] || { log "pg_restore stderr: $(head -5 /tmp/pg_restore.err 2>/dev/null || true)"; rm -f "$TMP"; die "dump is not a readable archive"; }

# Note what this does and does not prove: a custom-format dump lists every table it
# carries whether or not that table has rows, so this confirms the archive covers the
# schema -- it does not confirm the rows are there. That is the right check anyway,
# because on a fresh install `drives` and `charges` are legitimately empty, and a
# row-count threshold would fail the first fortnight of backups for no reason.
for table in cars drives charging_processes charges positions; do
  grep -qE "TABLE DATA public $table " <<<"$TOC" \
    || { rm -f "$TMP"; die "archive is missing table '$table' -- refusing to rotate backups"; }
done

mv -f "$TMP" "$OUT"
log "wrote $OUT ($(numfmt --to=iec "$SIZE" 2>/dev/null || echo "$SIZE bytes"))"

# ------------------------------------------------------------------ mirror --
if [[ -n "$MIRROR" ]]; then
  if [[ -d "$MIRROR" ]]; then
    cp -f "$OUT" "$MIRROR/" && log "mirrored to $MIRROR"
  else
    # Not fatal: a NAS being unreachable must not lose today's local backup.
    log "WARNING: mirror '$MIRROR' is not a directory -- skipped"
  fi
fi

# ------------------------------------------------------------------- prune --
# Only now, with a verified dump on disk.
mapfile -t OLD < <(ls -1t "$DEST"/teslamate-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))")
for f in "${OLD[@]:-}"; do
  [[ -n "$f" ]] || continue
  rm -f "$f" && log "pruned $(basename "$f")"
done

log "done -- $(ls -1 "$DEST"/teslamate-*.dump 2>/dev/null | wc -l | tr -d ' ') backups retained"
