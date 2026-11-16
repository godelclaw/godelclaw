#!/usr/bin/env bash
set -euo pipefail
runtime_root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
repo="$runtime_root"
mode=""
profile="auto"
state=""
prompt=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --mode) mode="${2:?missing mode}"; shift 2 ;;
        --profile) profile="${2:?missing profile}"; shift 2 ;;
        --state-dir) state="${2:?missing state directory}"; shift 2 ;;
        --repo) repo="${2:?missing repository}"; shift 2 ;;
        --prompt) prompt="${2:?missing prompt}"; shift 2 ;;
        --engine) [ "${2:-}" = cetta ] || { echo 'modes require CeTTa' >&2; exit 2; }; shift 2 ;;
        *) echo "unknown mode argument: $1" >&2; exit 2 ;;
    esac
done
case "$mode" in iter|omega) ;; *) echo 'choose --mode iter or omega' >&2; exit 2 ;; esac
case "$profile" in auto|standalone|supervised) ;; *) echo 'unknown mode profile' >&2; exit 2 ;; esac
repo="$(realpath -m -- "$repo")"
if [ "$profile" = auto ]; then
    if [ -n "${METTACLAW_TELEGRAM_SERVICE_SOCKET:-}" ]; then profile=supervised; else profile=standalone; fi
fi
if [ "$profile" = supervised ] && [ -z "${METTACLAW_TELEGRAM_SERVICE_SOCKET:-}" ]; then
    echo 'supervised mode requires its independent channel service' >&2; exit 2
fi
if [ -n "$prompt" ]; then
    [ "$profile" = standalone ] && [ -r "$prompt" ] || { echo '--prompt requires a readable standalone prompt' >&2; exit 2; }
    prompt="$(realpath -- "$prompt")"
fi
engine="${METTACLAW_MODE_CETTA_BIN:-${CETTA_BIN:-}}"
if [ -z "$engine" ] && [ -x "$runtime_root/.runtime/cetta/cetta" ]; then engine="$runtime_root/.runtime/cetta/cetta"; fi
if [ -z "$engine" ] && [ -n "${CETTA_ROOT:-}" ]; then engine="$CETTA_ROOT/cetta"; fi
[ -n "$engine" ] && [ -x "$engine" ] || { echo 'set CETTA_BIN to an installed native runtime' >&2; exit 2; }
engine="$(realpath -- "$engine")"
if [ -z "$state" ]; then
    if [ "$profile" = standalone ]; then
        state="${XDG_STATE_HOME:-$HOME/.local/state}/mettaclaw/$(basename "$repo")/standalone"
    elif [ -n "${METTACLAW_MODE_STATE_ROOT:-}" ]; then
        state="$METTACLAW_MODE_STATE_ROOT"
    else
        state="$(dirname "${METTACLAW_ENGINE_STATE_PATH:?missing instance state}")/modes"
    fi
fi
state="$(realpath -m -- "$state")"
mkdir -p -- "$state"
# Runtime ownership: one live cognitive process for this mode state.
exec 9>>"$state/cognition.lock"
flock -n 9 || { echo 'this mode state already has a live cognitive owner' >&2; exit 2; }

if [ "$profile" = standalone ]; then
    while IFS= read -r name; do
        case "$name" in METTACLAW_*|CETTA_IO_AUTH_*) unset "$name" ;; esac
    done < <(compgen -e)
    if [ "$mode" = iter ]; then
        export SYNTHETIC_MODEL="openai:${LLM_MODEL:-mlx-community/gemma-4-26b-a4b-it-4bit}"
        export OPENAI_BASE_URL="${BASE_URL:-http://192.168.64.1:2277/v1}"
        export OPENAI_API_KEY="${AI_API_KEY:-dummy}"
    else
        case "${OMEGA_provider:-Anthropic}" in
            OpenAIAPI)
                export SYNTHETIC_MODEL="${OMEGA_model:-${OMEGA_openaiapi_model:-qwen3.5:9b}}"
                export SYNTHETIC_BASE_URL="${OMEGA_openaiapi_url:-http://localhost:11434/v1/}"
                token_variable="${OMEGA_api_token_var:-OPENAIAPI_API_KEY}"
                [[ "$token_variable" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo 'invalid Omega credential variable' >&2; exit 2; }
                export SYNTHETIC_API_KEY="${!token_variable:-}" ;;
            ASICloud)
                export SYNTHETIC_MODEL="${OMEGA_model:-${OMEGA_asicloud_model:-minimax/minimax-m3}}"
                export SYNTHETIC_BASE_URL=https://inference.asicloud.cudos.org/v1
                export SYNTHETIC_API_KEY="${ASI_API_KEY:-}" ;;
            Anthropic)
                export SYNTHETIC_MODEL="${OMEGA_model:-${OMEGA_anthropic_model:-claude-opus-4-8}}" ;;
            OpenAI)
                export SYNTHETIC_MODEL="openai:${OMEGA_model:-${OMEGA_openai_model:-gpt-5.5}}" ;;
            *) echo 'unknown Omega provider' >&2; exit 2 ;;
        esac
    fi
fi
export METTACLAW_MODE_STATE_ROOT="$state"
export METTACLAW_MODE_RUNTIME="$state/$mode"
export METTACLAW_RUNTIME_ROOT="$runtime_root"
export METTACLAW_AGENT_ROOT="$repo"
export METTACLAW_ACTIVE_LOOP_MODE="$mode"
export METTACLAW_MODE_PROFILE="$profile"
export METTACLAW_MODE_PROMPT_ADDITION="$prompt"
export METTACLAW_ENGINE=cetta METTACLAW_ACTIVE_ENGINE=cetta
export CETTA_BIN="$engine"
mkdir -p -- "$METTACLAW_MODE_RUNTIME"
if [ "$profile" = standalone ]; then
    export METTACLAW_ENGINE_STATE_PATH="$METTACLAW_MODE_RUNTIME/engine"
    export METTACLAW_MODEL_STATE_PATH="$METTACLAW_MODE_RUNTIME/model.metta"
    export METTACLAW_COGNITIVE_HEALTH_PATH="$METTACLAW_MODE_RUNTIME/health.json"
    export METTACLAW_LLM_COMPLETION_PATH="$METTACLAW_MODE_RUNTIME/completion.json"
    export METTACLAW_PROVIDER_OBSERVATION_PATH="$METTACLAW_MODE_RUNTIME/providers.json"
    export METTACLAW_LIFECYCLE_PATH="$METTACLAW_MODE_RUNTIME/lifecycle.json"
    export METTACLAW_RECYCLE_REQUEST_PATH="$METTACLAW_MODE_RUNTIME/recycle.requested"
    export METTACLAW_NATIVE_LEDGER=1
    socket_root="${XDG_RUNTIME_DIR:-$HOME/.cache/godelclaw/run}"
    mkdir -p -- "$socket_root"
    state_digest="$(printf '%s' "$METTACLAW_MODE_RUNTIME" | sha256sum)"
    state_digest="${state_digest%% *}"
    export METTACLAW_INPUT_LEDGER_SOCKET="$socket_root/mode-$state_digest.sock"
fi
unset PYTHONHOME PYTHONPATH PYTHONNOUSERSITE
. "$runtime_root/scripts/native_runtime_env.sh"
"$engine" --no-results --lang petta --profile extended --import-mode ancestor-walk "$runtime_root/modes/prepare_native.metta"
printf 'Starting %s (%s); state: %s\n' "$mode" "$profile" "$METTACLAW_MODE_RUNTIME"
cd -- "$METTACLAW_MODE_RUNTIME"
exec "$engine" --value-bytes 268435456 --no-results --lang petta --profile extended --import-mode ancestor-walk \
    "$runtime_root/modes/$mode/native_main.metta"
