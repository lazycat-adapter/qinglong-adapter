#!/bin/sh
set -eu

legacy=$1
workspace=$2
pending="$workspace/.lazycat-migration-pending"

# The marker survives an interrupted copy. QingLong starts after this script,
# so a pending import can be retried without replacing an active workspace.
if [ ! -e "$pending" ]; then
    existing=$(find "$workspace" -mindepth 1 -maxdepth 1 -print -quit)
    [ -z "$existing" ] || exit 0
fi

legacy_data=
if [ -d "$legacy" ] && [ ! -L "$legacy" ] && [ -r "$legacy" ] && [ -x "$legacy" ]; then
    legacy_data=$(find "$legacy" -mindepth 1 -maxdepth 1 -print -quit)
fi
if [ -z "$legacy_data" ]; then
    [ -e "$pending" ] || exit 0
    echo "QingLong is waiting for read access to the legacy workspace to finish importing it." >&2
    exit 1
fi

touch "$pending"
cp -a -- "$legacy/." "$workspace/"
rm -- "$pending"
