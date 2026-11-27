#!/bin/sh
# A stand-in engine for the capability view: FAKE MODE COUNT [PREFIX...] ARG.
# It logs ARG to COUNT, then behaves as MODE (shell assignments) says: sleep
# past the probe deadline, fail, write raw bytes, or print its version or
# language inventory.
mode=$1
count=$2
for last; do :; done
printf '%s\n' "$last" >> "$count"
unset fail fail_on timeout timeout_on version inventory raw_stdout raw_stderr
. "$mode"
if [ -n "${timeout-}" ] || [ "${timeout_on-}" = "$last" ]; then sleep 4; fi
if [ -n "${fail-}" ] || [ "${fail_on-}" = "$last" ]; then exit 7; fi
if [ -n "${raw_stdout-}" ]; then printf '%b' "$raw_stdout"; exit 0; fi
if [ -n "${raw_stderr-}" ]; then printf '%b' "$raw_stderr" >&2; fi
if [ "$last" = --version ]; then
    printf '%s\n' "${version-fixture 日 r1}"
else
    printf '%s\n' "${inventory-Languages:
  petta implemented Native 日 🙂
  future planned Not yet
ignored implemented no indent}"
fi
