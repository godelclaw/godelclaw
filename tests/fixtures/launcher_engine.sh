#!/bin/sh
# A stand-in engine for the launcher tests. It answers the launcher's own
# bootstrap programs, records each other run in ENGINE_TEST_RESULT, and then
# behaves as FAKE_MODE asks:
#   (unset)  exit with CETTA_EXIT_CODE (0), optionally requesting a recycle
#            (CETTA_REQUEST_RECYCLE=1) or waiting for a signal (CETTA_WAIT_FOR_TERM=1);
#   channel  print the channel settings it inherited;
#   hold     start a long-lived child (its pid in PROBE_CHILD) and wait on it,
#            from the health check (FAKE_HOLD=health) or the program (probe).
set -eu
if [ "${FAKE_MODE:-}" = hold ]; then
    if [ "${FAKE_HOLD:-health}" = probe ]; then
        case "$*" in *scripts/memory_health_cetta.metta*) exit 0;; esac
    fi
    printf '%s\n' "$*" >"$PROBE_CHILD.args"
    sleep 300 &
    echo $! >"$PROBE_CHILD"
    wait
    exit 0
fi
if [ "${FAKE_MODE:-}" = channel ]; then
    printf '%s|%s|%s\n' "${METTACLAW_TELEGRAM_SERVICE_SOCKET:-}" "${METTACLAW_TELEGRAM_BOT_TOKEN:-}" "${TELEGRAM_BOT_TOKEN:-}"
    exit 0
fi
case "$*" in
    *scripts/config_to_env_cetta.metta*)
        printf 'native-bootstrap:%s\n' "$*" >>"$ENGINE_TEST_RESULT"; exit 0;;
    *scripts/memory_health_cetta.metta*) exit 0;;
    *scripts/loop_bootstrap_cetta.metta*)
        if [ "${METTACLAW_BOOT_OPERATION:-}" = recycle ]; then
            test -f "$METTACLAW_RECYCLE_REQUEST_PATH"
        else
            printf 'godelclaw\n'
        fi
        exit $?;;
esac
printf 'cetta:%s:%s\n' "$METTACLAW_ACTIVE_ENGINE" "$*" >>"$ENGINE_TEST_RESULT"
if [ "${CETTA_WAIT_FOR_TERM:-0}" = 1 ]; then
    : >"$CETTA_READY"
    trap 'exit 0' TERM INT HUP
    while :; do sleep 1; done
fi
if [ "${CETTA_REQUEST_RECYCLE:-0}" = 1 ]; then
    mkdir -p "$(dirname "$METTACLAW_RECYCLE_REQUEST_PATH")"
    : >"$METTACLAW_RECYCLE_REQUEST_PATH"
fi
exit "${CETTA_EXIT_CODE:-0}"
