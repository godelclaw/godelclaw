#!/usr/bin/env bash
set -euo pipefail

runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_root="${1:-$runtime_root}"
operation="${2:-publish}"
case "$operation" in print|publish|channel) ;; *) echo 'expected print, publish or channel' >&2; exit 2 ;; esac

engine="${CETTA_BOOTSTRAP_BIN:-${CETTA_BIN:-${METTACLAW_MODE_CETTA_BIN:-}}}"
if [ -z "$engine" ]; then
    if [ -n "${CETTA_ROOT:-}" ] && [ -x "$CETTA_ROOT/cetta" ]; then
        engine="$CETTA_ROOT/cetta"
    elif [ -x "$runtime_root/.runtime/cetta/cetta" ]; then
        engine="$runtime_root/.runtime/cetta/cetta"
    fi
fi
if [ -z "$engine" ] || [ ! -x "$engine" ]; then
    echo 'native configuration requires an installed CeTTa executable' >&2
    exit 2
fi

export METTACLAW_CONFIG_FILE="$config_root/config/local.toml"
export METTACLAW_CONFIG_ROOT="$config_root"
export METTACLAW_CONFIG_OPERATION="$operation"
export METTACLAW_CONFIG_CHANNEL="${3:-}"
export METTACLAW_CONFIG_PUBLISH=0
if [ "$operation" = publish ]; then export METTACLAW_CONFIG_PUBLISH=1; fi
exec "$engine" --no-results --lang petta --profile extended --import-mode relative \
    "$runtime_root/scripts/config_to_env_cetta.metta"
