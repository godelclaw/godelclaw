#!/bin/sh
# A stand-in for tmux: logs each call (monotonic seconds, then the arguments,
# one per tab, a backslash written \\ and a newline \n) to
# $TMUX_FIXTURE_DIR/calls, one call per line, and answers from files there named
# for the operation: OPERATION.stdout, OPERATION.stderr and OPERATION.code.
# The operation is the first argument, or "submit" for the Enter that sends a
# pasted line ("send-keys -t TARGET Enter").
dir=${TMUX_FIXTURE_DIR:?}
now=$(cut -d' ' -f1 /proc/uptime)
line=$now
for argument in "$@"; do
  escaped=$(printf '%s' "$argument" | sed -e ':a' -e '$!N' -e '$!ba' -e 's/\\/\\\\/g' -e 's/\n/\\n/g')
  line=$(printf '%s\t%s' "$line" "$escaped")
done
printf '%s\n' "$line" >> "$dir/calls"
operation=$1
if [ "$#" -eq 4 ] && [ "$1" = send-keys ] && [ "$2" = -t ] && [ "$4" = Enter ]; then operation=submit; fi
[ -f "$dir/$operation.stdout" ] && cat "$dir/$operation.stdout"
[ -f "$dir/$operation.stderr" ] && cat "$dir/$operation.stderr" >&2
code=0
[ -f "$dir/$operation.code" ] && code=$(cat "$dir/$operation.code")
exit "$code"
