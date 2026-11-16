#!/usr/bin/env bash
set -euo pipefail
repo='' state='' credential='' commands='' register=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --repo) repo="${2:?missing repository}"; shift 2 ;;
        --state) state="${2:?missing state}"; shift 2 ;;
        --credential) credential="${2:?missing credential file}"; shift 2 ;;
        --write-commands) commands="${2:?missing commands path}"; shift 2 ;;
        --register-menu) register=1; shift ;;
        *) echo 'invalid native Telegram launch arguments' >&2; exit 78 ;;
    esac
done
[ -n "$repo" ] || exit 78
repo="$(realpath -- "$repo")"
engine="${CETTA_BIN:-${CETTA_TELEGRAM_SERVICE_ROOT:?missing native engine}/cetta}"
write_only=0
if [ -n "$commands" ]; then
    write_only=1
    state="$(dirname -- "$commands")"
else
    [ -n "$state" ] && [ -n "$credential" ] || exit 78
    commands="$state/commands.metta"
    register=1
fi
mkdir -p -- "$state"
export METTACLAW_TELEGRAM_LAUNCH_REPO="$repo"
export METTACLAW_TELEGRAM_LAUNCH_COMMANDS="$commands"
export METTACLAW_TELEGRAM_LAUNCH_STATE="$state"
export METTACLAW_TELEGRAM_LAUNCH_CREDENTIAL="$credential"
export METTACLAW_TELEGRAM_LAUNCH_STAGE=settings
"$engine" --no-results --lang petta --profile extended --import-mode ancestor-walk "$repo/scripts/telegram_service_cetta.metta"
mapfile -t settings < "$state/channel-flags"
[ "${#settings[@]}" -eq 2 ] || exit 78
export METTACLAW_TELEGRAM_ALLOW_PRIVATE="${settings[0]}"
if [ "$write_only" -eq 0 ]; then
    export METTACLAW_TELEGRAM_LAUNCH_STAGE=argv
    "$engine" --no-results --lang petta --profile extended --import-mode ancestor-walk "$repo/scripts/telegram_service_cetta.metta"
fi
if [ "$register" -eq 1 ] && [ -n "$credential" ]; then
    # curl owns the credential file and expands it internally. Its bytes never
    # enter evaluator data, shell variables, process arguments or diagnostics.
    origin="${METTACLAW_TELEGRAM_MENU_ORIGIN:-https://api.telegram.org}"
    if curl --silent --fail --max-time 10 --output "$state/menu-response.json" \
        --variable "TG@$credential" --expand-url "$origin/bot{{TG:trim}}/setMyCommands" \
        --header 'Content-Type: application/json' --data-binary "@$state/menu.json" 2>/dev/null; then
        echo 'Telegram command menu submitted'
    else
        echo 'Telegram command menu unavailable; channel startup continues' >&2
    fi
fi
[ "$write_only" -eq 0 ] || exit 0
mapfile -d '' -t channel_command < "$state/channel-argv"
[ "${#channel_command[@]}" -gt 1 ] || exit 78
exec "${channel_command[@]}"
