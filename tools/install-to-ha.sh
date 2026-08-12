#!/bin/sh
# Install or update tapo_h500 into a Home Assistant config directory.
#
# HACS only accepts public GitHub repositories, so a self-hosted GitLab origin
# cannot be a custom repository. This does the same job without HACS: it copies
# the component in, and re-running it after a git pull is the update.
#
#   ./tools/install-to-ha.sh /config
#   git pull && ./tools/install-to-ha.sh /config     # update
#
# Run it on the machine that runs Home Assistant, from a clone of this repo.
# Restart Home Assistant afterwards; nothing here reloads it for you.
set -eu

CONFIG="${1:-}"
if [ -z "$CONFIG" ]; then
    echo "usage: $0 <home-assistant-config-dir>" >&2
    exit 2
fi
if [ ! -f "$CONFIG/configuration.yaml" ]; then
    echo "no configuration.yaml in $CONFIG — is that the config directory?" >&2
    exit 2
fi

SRC="$(CDPATH= cd -- "$(dirname -- "$0")/../custom_components" && pwd)"
if [ ! -f "$SRC/tapo_h500/manifest.json" ]; then
    echo "cannot find custom_components/tapo_h500 next to this script" >&2
    exit 2
fi

DEST="$CONFIG/custom_components"
mkdir -p "$DEST"

# Replace wholesale rather than merging, so files deleted upstream do not
# linger and get imported.
rm -rf "$DEST/tapo_h500.old"
[ -d "$DEST/tapo_h500" ] && mv "$DEST/tapo_h500" "$DEST/tapo_h500.old"
tar -C "$SRC" --exclude=__pycache__ -cf - tapo_h500 | tar -C "$DEST" -xf -
rm -rf "$DEST/tapo_h500.old"

VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    "$DEST/tapo_h500/manifest.json")
echo "installed tapo_h500 $VERSION into $DEST"
echo "restart Home Assistant, then add the integration under Settings >"
echo "Devices & services if this is a first install."
