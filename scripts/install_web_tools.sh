#!/usr/bin/env bash
# Install the agents' web tools for this user:
# - the Lightpanda page renderer that read-url runs, a pinned release checked
#   against its SHA-256, or a given build with --from FILE --sha256 HEX;
# - agent-browser, which browse uses to drive a Lightpanda session, from its
#   pinned npm platform package checked against its SHA-256;
# - a local SearXNG metasearch service for search: a podman container bound to
#   127.0.0.1, memory-capped, from a pinned image digest, run by systemd --user.
# Rerunning keeps the existing SearXNG settings (and their secret key).
set -euo pipefail

LIGHTPANDA_VERSION=1.0.5
LIGHTPANDA_URL="https://github.com/lightpanda-io/browser/releases/download/${LIGHTPANDA_VERSION}/lightpanda-x86_64-linux"
LIGHTPANDA_SHA256=0ec21fa4867ab9e82b35285ff4ab0e7336c6c1d89f26fe991f27d73c8a678bd7
AGENT_BROWSER_VERSION=0.43.0
AGENT_BROWSER_URL="https://registry.npmjs.org/@agent-browser/cli-linux-x64-gnu/-/cli-linux-x64-gnu-${AGENT_BROWSER_VERSION}.tgz"
AGENT_BROWSER_SHA256=aa00b14ee8294edfeb1bc01bc6418cf4b7e50d75b046a9ab497c10384207383f
SEARXNG_IMAGE="docker.io/searxng/searxng@sha256:27c6f6eca6aa5284eb8ec6ec40eee787cc4fe3014fad0c82059991a631ea741f"
SEARXNG_PORT="${SEARXNG_PORT:-8888}"
SEARXNG_MEMORY="${SEARXNG_MEMORY:-768m}"

from=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from) from="$2"; shift 2 ;;
        --sha256) LIGHTPANDA_SHA256="$2"; shift 2 ;;
        --skip-searxng) skip_searxng=1; shift ;;
        *) echo "usage: $0 [--from FILE --sha256 HEX] [--skip-searxng]" >&2; exit 2 ;;
    esac
done

lib="$HOME/.local/lib/godelclaw/lightpanda"
mkdir -p "$lib"
stage="$lib/.incoming-$$"
trap 'rm -f "$stage"' EXIT
if [[ -n "$from" ]]; then
    cp -- "$from" "$stage"
else
    curl -fsSL --max-time 300 -o "$stage" "$LIGHTPANDA_URL"
fi
actual="$(sha256sum "$stage" | cut -d' ' -f1)"
if [[ "$actual" != "$LIGHTPANDA_SHA256" ]]; then
    echo "lightpanda checksum mismatch: expected $LIGHTPANDA_SHA256, got $actual" >&2
    exit 1
fi
chmod 755 "$stage"
target="$lib/${actual:0:16}"
mkdir -p "$target"
mv -f "$stage" "$target/lightpanda"
ln -sfn "${actual:0:16}/lightpanda" "$lib/lightpanda"
echo "lightpanda: $("$lib/lightpanda" version) at $lib/lightpanda"

ab_lib="$HOME/.local/lib/godelclaw/agent-browser"
mkdir -p "$ab_lib"
ab_stage="$(mktemp -d "$ab_lib/.incoming-XXXXXX")"
trap 'rm -f "$stage"; rm -rf "$ab_stage"' EXIT
curl -fsSL --max-time 300 -o "$ab_stage/cli.tgz" "$AGENT_BROWSER_URL"
ab_actual="$(sha256sum "$ab_stage/cli.tgz" | cut -d' ' -f1)"
if [[ "$ab_actual" != "$AGENT_BROWSER_SHA256" ]]; then
    echo "agent-browser checksum mismatch: expected $AGENT_BROWSER_SHA256, got $ab_actual" >&2
    exit 1
fi
tar -xzf "$ab_stage/cli.tgz" -C "$ab_stage" package/bin/agent-browser
ab_target="$ab_lib/${ab_actual:0:16}"
mkdir -p "$ab_target"
install -m 755 "$ab_stage/package/bin/agent-browser" "$ab_target/agent-browser"
ln -sfn "${ab_actual:0:16}/agent-browser" "$ab_lib/agent-browser"
echo "agent-browser: $("$ab_lib/agent-browser" --version) at $ab_lib/agent-browser"

if [[ -n "${skip_searxng:-}" ]]; then exit 0; fi

config="$HOME/.config/godelclaw/searxng"
mkdir -p "$config"
chmod 700 "$config"
if [[ ! -f "$config/settings.yml" ]]; then
    umask 077
    cat > "$config/settings.yml" <<EOF
use_default_settings: true
general:
  instance_name: "agents-local"
server:
  secret_key: "$(head -c 32 /dev/urandom | sha256sum | cut -c1-48)"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
  public_instance: false
  image_proxy: false
search:
  safe_search: 0
  default_lang: "en"
  formats: [html, json]
engines:
  - name: bing
    disabled: true
  - name: brave
    disabled: false
  - name: qwant
    disabled: false
EOF
fi

units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/godelclaw-searxng.service" <<EOF
[Unit]
Description=Local SearXNG metasearch for the agents (127.0.0.1:${SEARXNG_PORT})
After=network-online.target

[Service]
ExecStartPre=-/usr/bin/podman rm -f godelclaw-searxng
ExecStart=/usr/bin/podman run --rm --name godelclaw-searxng --memory=${SEARXNG_MEMORY} --memory-swap=${SEARXNG_MEMORY} -p 127.0.0.1:${SEARXNG_PORT}:8080 -v ${config}:/etc/searxng:Z ${SEARXNG_IMAGE}
ExecStop=/usr/bin/podman stop -t 10 godelclaw-searxng
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
podman pull -q "$SEARXNG_IMAGE" >/dev/null
systemctl --user daemon-reload
systemctl --user enable --now godelclaw-searxng.service
for _ in $(seq 1 30); do
    if curl -fs --max-time 2 "http://127.0.0.1:${SEARXNG_PORT}/healthz" >/dev/null; then
        echo "searxng: healthy at http://127.0.0.1:${SEARXNG_PORT}"
        exit 0
    fi
    sleep 1
done
echo "searxng: not healthy after 30 s; see journalctl --user -u godelclaw-searxng" >&2
exit 1
