#!/usr/bin/env bash
# Put the add-on on a Home Assistant OS host as a LOCAL add-on.
#
# The Supervisor builds a local add-on from a directory under /addons whose contents are
# the build context: the manifest, the Dockerfile and everything COPY'd. In this repo the
# manifest lives in addon/ and the code in bridge/, so this assembles the two into
# /addons/ha-teslamate-bridge on the host. No rsync is assumed (HA OS has none); tar over
# ssh is enough.
#
#   ./deploy.sh                       # hassio@homeassistant.local, key ~/.ssh/ha_claude
#   ./deploy.sh user@host             # another host
#   KEY=~/.ssh/other ./deploy.sh      # another key
#
# Afterwards, in Home Assistant: Settings -> Add-ons -> Add-on Store -> (top right)
# Check for updates -> "Local add-ons" -> HA TeslaMate Bridge -> Install -> Start.
# Installing and starting is a Supervisor action and cannot be done from here.
set -euo pipefail

HOST="${1:-hassio@homeassistant.local}"
KEY="${KEY:-$HOME/.ssh/ha_claude}"
DEST="${DEST:-/addons/ha-teslamate-bridge}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$HERE"
test -f addon/config.yaml && test -f addon/Dockerfile && test -d bridge

echo "deploying to ${HOST}:${DEST}"
tar --exclude='__pycache__' --exclude='*.pyc' -cf - -C addon . \
  | ssh -i "$KEY" "$HOST" "sudo mkdir -p '$DEST' && sudo tar -C '$DEST' -xf -"
tar --exclude='__pycache__' --exclude='*.pyc' -cf - bridge \
  | ssh -i "$KEY" "$HOST" "sudo tar -C '$DEST' -xf - && sudo chmod a+x '$DEST/run' && ls -la '$DEST' '$DEST/bridge'"
echo "done. Now install it from the add-on store under 'Local add-ons'."
