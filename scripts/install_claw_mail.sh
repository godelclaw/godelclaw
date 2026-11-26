#!/bin/sh
# Install the claw-mail client and its Claude skill for this account from this
# checkout. The client runs on the CeTTa engine named by --engine (or CETTA_BIN):
# its files and a launcher naming that engine go into a content-addressed
# release directory beside the bin directory, and bin/claw-mail links to the
# launcher.
#
# usage: install_claw_mail.sh [--engine CETTA] [--bin-dir DIR] [--skill-dir DIR]
#          [--no-skill] [--config PATH] [--socket SOCKET] [--agent NAME] [--force-links]
#
# --socket and --agent write a new private mail.toml (mode 0600); an existing
# one is never changed. --force-links replaces existing symbolic links, never
# regular files.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd -P)
engine=${CETTA_BIN:-}
bin_dir=$HOME/.local/bin
skill_dir=$HOME/.claude/skills
skill=yes
config=${XDG_CONFIG_HOME:-$HOME/.config}/godelclaw/mail.toml
socket=
agent=
force=no

fail() { echo "install_claw_mail: error: $*" >&2; exit 2; }
needs() { [ $# -ge 2 ] || fail "argument $1: expected one argument"; }
while [ $# -gt 0 ]; do
  case $1 in
    --engine|--cetta) needs "$@"; engine=$2; shift 2 ;;
    --bin-dir) needs "$@"; bin_dir=$2; shift 2 ;;
    --skill-dir) needs "$@"; skill_dir=$2; shift 2 ;;
    --no-skill) skill=no; shift ;;
    --config) needs "$@"; config=$2; shift 2 ;;
    --socket) needs "$@"; socket=$2; shift 2 ;;
    --agent) needs "$@"; agent=$2; shift 2 ;;
    --force-links) force=yes; shift ;;
    -h|--help) sed -n '2,15s/^# \{0,1\}//p' "$0"; exit 0 ;;
    *) fail "unrecognized arguments: $1" ;;
  esac
done
[ -n "$engine" ] || fail "choose the engine with --engine or CETTA_BIN"
case $engine in /*) ;; *) engine=$(pwd -P)/$engine ;; esac
[ -x "$engine" ] || fail "$engine is not an executable engine"
if [ -n "$socket$agent" ] && { [ -e "$config" ] || [ -L "$config" ]; }; then
  fail "$config already exists; edit it to update its settings"
fi

quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# The release is named by the digest of what it holds: the two client files
# and the launcher's engine.
digest=$( { sha256sum "$root/scripts/claw_mail_cetta.metta" "$root/scripts/shared_mail.metta" | cut -d' ' -f1
            printf '%s\n' "$engine"; } | sha256sum | cut -d' ' -f1)
mkdir -p "$bin_dir"
release=$(cd "$bin_dir/.." && pwd -P)/share/claw-mail/$digest
mkdir -p "$release"
for name in claw_mail_cetta.metta shared_mail.metta; do
  if [ -e "$release/$name" ]; then
    cmp -s "$root/scripts/$name" "$release/$name" || fail "installed mail snapshot has changed: $release/$name"
  else
    cp "$root/scripts/$name" "$release/$name.$$" && mv "$release/$name.$$" "$release/$name"
  fi
done
launcher="#!/bin/sh
exec $(quote "$engine") --no-results --lang petta $(quote "$release/claw_mail_cetta.metta") \"\$@\""
if [ -e "$release/claw-mail" ]; then
  [ "$(cat "$release/claw-mail")" = "$launcher" ] || fail "installed mail snapshot has changed: $release/claw-mail"
else
  printf '%s\n' "$launcher" > "$release/claw-mail.$$" && chmod 0755 "$release/claw-mail.$$" \
    && mv "$release/claw-mail.$$" "$release/claw-mail"
fi
printf '{\n  "identity": "%s",\n  "engine": "%s",\n  "files": ["claw-mail", "claw_mail_cetta.metta", "shared_mail.metta"]\n}\n' \
  "$digest" "$engine" > "$release/manifest.json"

link() {
  source=$1 destination=$2
  if [ -L "$destination" ] && [ "$(readlink -f "$destination")" = "$(readlink -f "$source")" ]; then return 0; fi
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    { [ "$force" = yes ] && [ -L "$destination" ]; } || fail "$destination exists; keep it or choose another destination"
  fi
  mkdir -p "$(dirname "$destination")"
  temporary=$(dirname "$destination")/.$(basename "$destination")-$$
  ln -s "$(readlink -f "$source")" "$temporary"
  mv -T "$temporary" "$destination"
  echo "$destination"
}
link "$release/claw-mail" "$bin_dir/claw-mail"
[ "$skill" = no ] || link "$root/.claude/skills/claw-mail" "$skill_dir/claw-mail"

if [ -n "$socket$agent" ]; then
  mkdir -p "$(dirname "$config")"
  toml() { printf '%s = "%s"\n' "$1" "$(printf '%s' "$2" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }
  # A default ACL on the directory overrides the umask, so the mode is set
  # explicitly as well.
  ( umask 077; set -C
    { [ -z "$socket" ] || toml socket "$socket"; [ -z "$agent" ] || toml agent "$agent"; } > "$config" ) \
    || fail "$config already exists; edit it to update its settings"
  chmod 0600 "$config" || fail "could not make $config private"
  echo "$config"
fi
