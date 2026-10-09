#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
PETTA_ROOT="${PETTA_ROOT:-${HOME:-}/repos/PeTTa}"
export METTACLAW_PETTA_RUNNER="${METTACLAW_PETTA_RUNNER:-$PETTA_ROOT/run.sh}"
PETTA_PY_ENV="${PETTA_PY_ENV:-${HOME:-}/miniforge3/envs/petta}"
CETTA_ROOT="${CETTA_ROOT:-${HOME:-}/repos/CeTTa-runtime}"
STATE_HOME="${XDG_STATE_HOME:-${HOME:-}/.local/state}"
INSTANCE="${METTACLAW_INSTANCE:-$(basename "$ROOT")}"
export METTACLAW_INSTANCE="$INSTANCE"

# Alternate .metta targets are tests or utilities, not the persistent agent.
# Capture caller-supplied state paths before local configuration is loaded;
# unspecified mutable state is redirected to one disposable directory.  This
# prevents a semantic probe from changing the live mode, working set, history,
# transport offset, or receipts merely because it imports the real loop.
REQUESTED_TARGET="${1:-run.metta}"
CALLER_OBSERVED_STRATEGY="${METTACLAW_OBSERVED_STRATEGY:-0}"
CALLER_FAIR_GOALS="${METTACLAW_FAIR_GOALS:-0}"
CALLER_TELEGRAM_SERVICE_SOCKET="${METTACLAW_TELEGRAM_SERVICE_SOCKET:-}"
CALLER_TELEGRAM_CLIENT="${METTACLAW_TELEGRAM_CLIENT:-python}"
CALLER_METTACLAW_TELEGRAM_BOT_TOKEN="${METTACLAW_TELEGRAM_BOT_TOKEN:-}"
CALLER_TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
case "$REQUESTED_TARGET" in
    run.metta|"$ROOT/run.metta") NONLIVE_TARGET=0 ;;
    telegram-control) NONLIVE_TARGET=0 ;;
    *) NONLIVE_TARGET=1 ;;
esac

# A paired release holds only cognitive launchers while both instances are
# staged and checked. Operator controls and isolated probes remain available.
if [ "$NONLIVE_TARGET" = 0 ] && [ "$REQUESTED_TARGET" != telegram-control ]; then
    while [ -f "$ROOT/.runtime-update-hold" ]; do sleep 1; done
fi
STATE_PATH_NAMES=(
    METTACLAW_ENGINE_STATE_PATH METTACLAW_ENGINE_FAILURE_PATH
    METTACLAW_TELEGRAM_OFFSET_PATH
    METTACLAW_TELEGRAM_LOG_PATH METTACLAW_TELEGRAM_HEALTH_PATH
    METTACLAW_COGNITIVE_HEALTH_PATH METTACLAW_LIFECYCLE_PATH
    METTACLAW_DEPLOYMENT_STATE_PATH METTACLAW_RECYCLE_REQUEST_PATH
    METTACLAW_WORKING_SET_PATH METTACLAW_HISTORY_PATH METTACLAW_PROMPT_PATH
    METTACLAW_LOOP_MODE_PATH METTACLAW_FUEL_MODE_PATH
    METTACLAW_ENERGY_PATH METTACLAW_MODEL_STATE_PATH
    METTACLAW_ANTHROPIC_USAGE_PATH METTACLAW_EFFECT_RECEIPT_PATH
    METTACLAW_STIMULUS_FRONTIER_PATH METTACLAW_WAKE_REQUEST_PATH
    METTACLAW_MEMORY_LOG_DIR METTACLAW_CHROMA_DIR
    METTACLAW_TELEGRAM_ATTACHMENTS_DIR METTACLAW_SELFMOD_LOG
    METTACLAW_SELFMOD_PROPOSAL_STORE
    METTACLAW_ITER_PROCESS_DIR METTACLAW_JOBS_DIR
    METTACLAW_PINS_PATH METTACLAW_GOALS_PATH
    METTACLAW_PROVIDER_OBSERVATION_PATH
    METTACLAW_LLM_LEDGER_PATH METTACLAW_LLM_COMPLETION_PATH
    METTACLAW_STRATEGY_PATH
)
declare -A CALLER_STATE_SET=()
declare -A CALLER_STATE_VALUE=()
for state_name in "${STATE_PATH_NAMES[@]}"; do
    if [[ -v $state_name ]]; then
        CALLER_STATE_SET["$state_name"]=1
        CALLER_STATE_VALUE["$state_name"]="${!state_name}"
    fi
done

if [ "${METTACLAW_SKIP_INITIALIZE:-0}" != "1" ] && [ -x "$ROOT/initialize.sh" ]; then
    "$ROOT/initialize.sh" >/dev/null
fi

# Configuration rendering is lightweight and independent of initialization.
# A skipped initializer must not leave a stale generated environment at boot.
if [ -f "$ROOT/config/local.toml" ] && [ -f "$ROOT/scripts/config_ctl.py" ]; then
    env -u PYTHONHOME -u PYTHONPATH "${PYTHON:-python3}" "$ROOT/scripts/config_ctl.py" render >/dev/null
fi

if [ -f "$ROOT/.env" ]; then
    set -a
    . "$ROOT/.env"
    set +a
fi

if [ -f "$ROOT/config/secrets.env" ]; then
    set -a
    . "$ROOT/config/secrets.env"
    set +a
fi

TEST_STATE_DIR=""
if [ "$NONLIVE_TARGET" = 1 ]; then
    export METTACLAW_OBSERVED_STRATEGY="$CALLER_OBSERVED_STRATEGY"
    export METTACLAW_FAIR_GOALS="$CALLER_FAIR_GOALS"
    # Configuration-only channel credentials belong to the persistent agent.
    # A probe can opt into its own fixture through the caller environment.
    export METTACLAW_TELEGRAM_SERVICE_SOCKET="$CALLER_TELEGRAM_SERVICE_SOCKET"
    export METTACLAW_TELEGRAM_CLIENT="$CALLER_TELEGRAM_CLIENT"
    export METTACLAW_TELEGRAM_BOT_TOKEN="$CALLER_METTACLAW_TELEGRAM_BOT_TOKEN"
    export TELEGRAM_BOT_TOKEN="$CALLER_TELEGRAM_BOT_TOKEN"
    TEST_STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pettaclaw-probe.XXXXXX")"
    trap 'rm -rf -- "$TEST_STATE_DIR"' EXIT
    for state_name in "${STATE_PATH_NAMES[@]}"; do
        if [ "${CALLER_STATE_SET[$state_name]:-0}" = 1 ]; then
            printf -v "$state_name" '%s' "${CALLER_STATE_VALUE[$state_name]}"
        else
            printf -v "$state_name" '%s/%s' "$TEST_STATE_DIR" "$state_name"
        fi
        export "$state_name"
    done
    if [ "${CALLER_STATE_SET[METTACLAW_LIFECYCLE_PATH]:-0}" != 1 ]; then
        printf '%s\n' '{"schema":1,"state":"running"}' \
            >"$METTACLAW_LIFECYCLE_PATH"
    fi
    if [ "${CALLER_STATE_SET[METTACLAW_DEPLOYMENT_STATE_PATH]:-0}" != 1 ]; then
        probe_now="$(date +%s)"
        printf '%s\n' \
            "{\"candidate\":\"test-probe\",\"last_observation\":{\"observed_at\":$probe_now,\"head\":\"test-probe\",\"active\":true,\"problems\":[]}}" \
            >"$METTACLAW_DEPLOYMENT_STATE_PATH"
    fi
fi

# The persisted selection is deliberately tiny: one validated engine name.
# With no state, SWI-PeTTa remains the stable default. The active value is
# exported separately so the Telegram UI can distinguish this process from a
# newly requested engine that will take effect after recycling.
export METTACLAW_ENGINE_STATE_PATH="${METTACLAW_ENGINE_STATE_PATH:-$STATE_HOME/$INSTANCE/engine}"
export METTACLAW_ENGINE_FAILURE_PATH="${METTACLAW_ENGINE_FAILURE_PATH:-$STATE_HOME/$INSTANCE/engine-failure}"
export METTACLAW_RECYCLE_REQUEST_PATH="${METTACLAW_RECYCLE_REQUEST_PATH:-$STATE_HOME/$INSTANCE/recycle.requested}"
# Acknowledge before capturing either selection. Anything requested during
# startup belongs to this process and must survive until its boundary.
if [ "$REQUESTED_TARGET" = run.metta ] || [ "$REQUESTED_TARGET" = "$ROOT/run.metta" ]; then
    rm -f "$METTACLAW_RECYCLE_REQUEST_PATH"
fi
ENGINE_DEFAULT="${METTACLAW_ENGINE:-petta}"
SELECTED_ENGINE="$ENGINE_DEFAULT"
HEAL_ENGINE_SELECTION=0
if [ -f "$METTACLAW_ENGINE_STATE_PATH" ]; then
    IFS= read -r SELECTED_ENGINE <"$METTACLAW_ENGINE_STATE_PATH" || true
fi
case "$SELECTED_ENGINE" in
    petta|cetta) ;;
    pleatta)
        echo "PLeaTTa is temporarily disabled because its current integration is not functional; using petta" >&2
        SELECTED_ENGINE=petta
        HEAL_ENGINE_SELECTION=1
        ;;
    *)
        echo "invalid persisted engine '$SELECTED_ENGINE'; using petta" >&2
        SELECTED_ENGINE=petta
        HEAL_ENGINE_SELECTION=1
        ;;
esac
export METTACLAW_ENGINE="$SELECTED_ENGINE"
export METTACLAW_ACTIVE_ENGINE="$SELECTED_ENGINE"

if [ -d "$PETTA_PY_ENV" ]; then
    export LD_LIBRARY_PATH="$PETTA_PY_ENV/lib:${LD_LIBRARY_PATH:-}"
    export PYTHONHOME="$PETTA_PY_ENV"
    export PYTHONNOUSERSITE=1
    # Keep PATH in sync with PYTHONHOME: a bare `python3` (e.g. from a shell
    # skill or subprocess) must resolve to THIS env's interpreter, or it loads
    # the wrong stdlib and dies with "ModuleNotFoundError: encodings".
    export PATH="$PETTA_PY_ENV/bin:$HOME/.local/bin:$PATH"
    # ~/.local/bin: systemd user services run no login shell, so ~/.profile
    # never adds it — without this line the agent lives in a poorer PATH
    # than any interactive shell (claude, cc-telegram, user tools all live there).
else
    echo "warning: PETTA_PY_ENV ($PETTA_PY_ENV) not found; the Python bridge will likely fail. Set petta_py_env in config/local.toml." >&2
fi

# Keep py-call module resolution local and reproducible. Explicitly include the
# checked-out semantic-memory bridge so both engines resolve it the same way.
# Do not inherit an external PYTHONPATH, which would mix venvs again.
export PYTHONPATH="$ROOT/src:$ROOT/channels:$ROOT/repos/petta_lib_chromadb"

export SYNTHETIC_MODEL="${SYNTHETIC_MODEL:-syn:large:text}"
export METTACLAW_CHROMA_DIR="${METTACLAW_CHROMA_DIR:-$ROOT/chroma_db}"
export METTACLAW_CHROMA_COLLECTION="${METTACLAW_CHROMA_COLLECTION:-memories}"
export METTACLAW_CHROMA_METRIC="${METTACLAW_CHROMA_METRIC:-cosine}"
export METTACLAW_CHROMA_SYNC_THRESHOLD="${METTACLAW_CHROMA_SYNC_THRESHOLD:-20}"
export METTACLAW_MEMORY_LOG_DIR="${METTACLAW_MEMORY_LOG_DIR:-$ROOT/memory/remembered}"
export METTACLAW_TELEGRAM_OFFSET_PATH="${METTACLAW_TELEGRAM_OFFSET_PATH:-$STATE_HOME/$INSTANCE/telegram/offset}"
export METTACLAW_TELEGRAM_LOG_PATH="${METTACLAW_TELEGRAM_LOG_PATH:-$STATE_HOME/$INSTANCE/telegram/updates.jsonl}"
export METTACLAW_COGNITIVE_HEALTH_PATH="${METTACLAW_COGNITIVE_HEALTH_PATH:-$STATE_HOME/$INSTANCE/cognitive-health.json}"
export METTACLAW_LLM_LEDGER_PATH="${METTACLAW_LLM_LEDGER_PATH:-$STATE_HOME/$INSTANCE/llm-budget.sqlite3}"
export METTACLAW_LLM_COMPLETION_PATH="${METTACLAW_LLM_COMPLETION_PATH:-$STATE_HOME/$INSTANCE/llm-completion.json}"
export METTACLAW_STRATEGY_PATH="${METTACLAW_STRATEGY_PATH:-$STATE_HOME/$INSTANCE/observed-strategy.sqlite3}"
export METTACLAW_LIFECYCLE_PATH="${METTACLAW_LIFECYCLE_PATH:-$STATE_HOME/$INSTANCE/lifecycle.json}"
export METTACLAW_DEPLOYMENT_STATE_PATH="${METTACLAW_DEPLOYMENT_STATE_PATH:-$STATE_HOME/$INSTANCE/deployment.json}"
export METTACLAW_STIMULUS_FRONTIER_PATH="${METTACLAW_STIMULUS_FRONTIER_PATH:-$STATE_HOME/$INSTANCE/stimulus-frontier}"
# The Telegram command responder asks the loop to end a rest through this file.
export METTACLAW_WAKE_REQUEST_PATH="${METTACLAW_WAKE_REQUEST_PATH:-$STATE_HOME/$INSTANCE/wake.requested}"
if [ "$NONLIVE_TARGET" = 0 ]; then
    export METTACLAW_EFFECT_RECEIPT_PATH="${METTACLAW_EFFECT_RECEIPT_PATH:-$STATE_HOME/$INSTANCE/effect-receipts.jsonl}"
fi
export METTACLAW_EMBED_MODEL="${METTACLAW_EMBED_MODEL:-}"
export METTACLAW_EMBED_DIM="${METTACLAW_EMBED_DIM:-1024}"
export METTACLAW_EMBED_DEVICE="${METTACLAW_EMBED_DEVICE:-auto}"
export METTACLAW_EMBED_QUERY_PROMPT_NAME="${METTACLAW_EMBED_QUERY_PROMPT_NAME:-query}"
export METTACLAW_EMBED_NORMALIZE="${METTACLAW_EMBED_NORMALIZE:-1}"
export METTACLAW_EMBED_MAX_TOKENS="${METTACLAW_EMBED_MAX_TOKENS:-4096}"
# One shared embedding daemon for the whole machine; never load Qwen in-process.
export METTACLAW_EMBED_ENDPOINT="${METTACLAW_EMBED_ENDPOINT:-http://127.0.0.1:8876}"

# Cooperative heap recycling. A requester creates the flag; the running loop
# exits after its current turn; this new process removes the acknowledged flag.
export METTACLAW_RECYCLE_REQUEST_PATH="${METTACLAW_RECYCLE_REQUEST_PATH:-$STATE_HOME/$INSTANCE/recycle.requested}"
mkdir -p \
    "$(dirname "$METTACLAW_RECYCLE_REQUEST_PATH")" \
    "$(dirname "$METTACLAW_ENGINE_STATE_PATH")" \
    "$(dirname "$METTACLAW_ENGINE_FAILURE_PATH")" \
    "$(dirname "$METTACLAW_TELEGRAM_OFFSET_PATH")" \
    "$(dirname "$METTACLAW_TELEGRAM_LOG_PATH")" \
    "$(dirname "$METTACLAW_COGNITIVE_HEALTH_PATH")" \
    "$(dirname "$METTACLAW_LIFECYCLE_PATH")" \
    "$(dirname "$METTACLAW_DEPLOYMENT_STATE_PATH")"

if [ "$HEAL_ENGINE_SELECTION" = 1 ]; then
    temporary="${METTACLAW_ENGINE_STATE_PATH}.tmp.$$"
    umask 077
    printf '%s\n' petta >"$temporary"
    chmod 600 "$temporary"
    mv -f "$temporary" "$METTACLAW_ENGINE_STATE_PATH"
fi

mkdir -p "$METTACLAW_CHROMA_DIR" "$METTACLAW_MEMORY_LOG_DIR" "$ROOT/chat" "$ROOT/episodes"

# Local imports (./src, ./memory, ./lib_mettaclaw) and git-import's default
# ./repos clone dir resolve relative to CWD, so always launch from the repo
# root regardless of where this script was invoked from.
cd "$ROOT"

# The Telegram command responder (channels/telegram_control.metta) is a
# process of its own beside cognition, in the same configured environment.
# It is MeTTa only, so it always runs on CeTTa, and it touches none of the
# loop's lifecycle: no recycle acknowledgement, no engine fallback.
if [ "$REQUESTED_TARGET" = telegram-control ]; then
    CETTA_BIN="${CETTA_BIN:-$CETTA_ROOT/cetta}"
    exec "$CETTA_BIN" --lang petta --import-mode ancestor-walk \
        "$ROOT/channels/telegram_control_main.metta" default
fi

# Default target is the agent loop (run.metta). Pass an alternate repo-relative
# or absolute .metta to run it in this same configured environment, e.g.
#   ./run.sh smoketest.metta   # import-only check; does NOT start the loop
TARGET="${1:-run.metta}"
case "$TARGET" in /*) ;; *) TARGET="$ROOT/$TARGET" ;; esac

persist_engine_selection() {
    local engine="$1"
    local temporary="${METTACLAW_ENGINE_STATE_PATH}.tmp.$$"
    umask 077
    printf '%s\n' "$engine" >"$temporary"
    chmod 600 "$temporary"
    mv -f "$temporary" "$METTACLAW_ENGINE_STATE_PATH"
}

record_engine_failure() {
    local status="$1"
    local reason="$2"
    local temporary="${METTACLAW_ENGINE_FAILURE_PATH}.tmp.$$"
    umask 077
    {
        printf 'schema=1\n'
        printf 'observed_at=%s\n' "$(date +%s)"
        printf 'engine=%s\n' "$METTACLAW_ENGINE"
        printf 'status=%s\n' "$status"
        printf 'reason=%s\n' "$reason"
        printf 'binary=%s\n' "${CETTA_BIN:-$CETTA_ROOT/cetta}"
    } >"$temporary"
    chmod 600 "$temporary"
    mv -f "$temporary" "$METTACLAW_ENGINE_FAILURE_PATH"
}

fallback_to_petta() {
    local status="$1"
    local reason="$2"
    if ! record_engine_failure "$status" "$reason"; then
        echo "warning: could not persist engine failure receipt" >&2
    fi
    echo "engine '$METTACLAW_ENGINE' failed ($reason, status $status)" >&2
    if [ "$TARGET" != "$ROOT/run.metta" ] || [ "${METTACLAW_ENGINE_FALLBACK:-none}" != petta ]; then
        echo "engine selection preserved; inspect the failure or explicitly select another engine" >&2
        exit "$status"
    fi
    echo "restoring stable engine 'petta'" >&2
    persist_engine_selection petta
    export METTACLAW_ENGINE=petta
    export METTACLAW_ACTIVE_ENGINE=petta
    # Re-enter mode dispatch; fallback must not always start the framework.
    exec bash "$ROOT/run.sh"
}

if [ "$TARGET" = "$ROOT/run.metta" ] && [ -f "$ROOT/modes/launch.py" ]; then
    LOOP_MODE="$("$PETTA_PY_ENV/bin/python3" -c 'import loop_modes; print(loop_modes.current_mode())')"
    export METTACLAW_ACTIVE_LOOP_MODE="$LOOP_MODE"
    case "$LOOP_MODE" in
        iter|omega)
            exec "$PETTA_PY_ENV/bin/python3" "$ROOT/modes/launch.py" \
                --repo "$ROOT" --mode "$LOOP_MODE" --engine "$METTACLAW_ENGINE"
            ;;
        *)
            "$PETTA_PY_ENV/bin/python3" -c \
                'import os,loop_modes; loop_modes.record_active(os.environ["METTACLAW_ACTIVE_LOOP_MODE"],os.getppid())'
            ;;
    esac
fi

# Memory-index startup diagnostic and best-effort hard-drift recovery.
# Chroma flushes its HNSW index only every sync_threshold writes; an index far
# behind the write-ahead log segfaults the process when the backlog is replayed
# at query time. This subprocess reports to the service journal, not the model
# prompt. It attempts catch-up only at dangerous drift and reports measured
# before/after state. It remains non-fatal so diagnostics cannot prevent boot.
PYTHONPATH="$ROOT/src:$ROOT/repos/petta_lib_chromadb" \
    "$PETTA_PY_ENV/bin/python3" "$ROOT/src/memory_health.py" 2>&1 || true

case "$METTACLAW_ENGINE" in
    petta)
        if [ "$NONLIVE_TARGET" = 1 ]; then
            "$PETTA_ROOT/run.sh" "$TARGET" default
            exit $?
        fi
        exec "$PETTA_ROOT/run.sh" "$TARGET" default
        ;;
    cetta)
        CETTA_BIN="${CETTA_BIN:-$CETTA_ROOT/cetta}"
        if [ ! -x "$CETTA_BIN" ]; then
            fallback_to_petta 2 "CeTTa executable unavailable"
        fi
        if [ "$TARGET" != "$ROOT/run.metta" ]; then
            "$CETTA_BIN" --lang petta --import-mode ancestor-walk "$TARGET" default
            exit $?
        fi
        # systemd stops the launcher and its CeTTa child as one control group.
        # CeTTa may turn that TERM into a clean exit; without this independent
        # witness the launcher mistakes an intentional service lifecycle event
        # for an engine failure and permanently rewrites the selection to
        # PeTTa.  The trap changes no crash/fallback policy: it only remembers
        # that this launcher itself was explicitly asked to stop.
        shutdown_requested=0
        trap 'shutdown_requested=1' TERM INT HUP
        set +e
        # The bootstrap imports the whole program exactly as run.metta does;
        # listing its modules here as well would load every equation twice.
        "$CETTA_BIN" --lang petta --import-mode ancestor-walk \
            "$ROOT/cetta_bootstrap.metta" \
            "$ROOT/cetta_run.metta" \
            default
        status=$?
        set -e
        trap - TERM INT HUP
        if [ "$shutdown_requested" -eq 1 ]; then
            exit 0
        fi
        if [ "$status" -eq 0 ] && "$PETTA_PY_ENV/bin/python3" -c \
            'import engine_modes; raise SystemExit(0 if engine_modes.recycle_requested() else 1)'; then
            exit 0
        fi
        if [ "$status" -eq 0 ]; then
            status=1
            fallback_to_petta "$status" "CeTTa stopped without a recycle request"
        fi
        fallback_to_petta "$status" "CeTTa process exited"
        ;;
    *)
        echo "unknown METTACLAW_ENGINE: $METTACLAW_ENGINE" >&2
        exit 2
        ;;
esac
