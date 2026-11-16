#!/usr/bin/env bash
# Host-owned credential descriptors contain names and URL scopes, never key bytes.
export METTACLAW_REQUIRE_NATIVE_CORE="${METTACLAW_REQUIRE_NATIVE_CORE:-1}"
native_auth_descriptor() {
    local alias="$1" credential_name="$2" base="$3" header="$4" prefix="$5"
    while [[ "$base" == */ ]]; do base="${base%/}"; done
    export "CETTA_IO_AUTH_${alias}_URL_PREFIX=$base/"
    export "CETTA_IO_AUTH_${alias}_ENV=$credential_name"
    export "CETTA_IO_AUTH_${alias}_HEADER=$header"
    export "CETTA_IO_AUTH_${alias}_PREFIX=$prefix"
}
native_auth_descriptor LLM_SYNTHETIC SYNTHETIC_API_KEY "${SYNTHETIC_BASE_URL:-https://api.synthetic.new/openai/v1}" Authorization 'Bearer '
native_auth_descriptor LLM_KIMI KIMI_API_KEY "${KIMI_BASE_URL:-https://api.kimi.ai/coding/v1}" Authorization 'Bearer '
native_auth_descriptor LLM_OPENAI OPENAI_API_KEY "${OPENAI_BASE_URL:-https://api.openai.com/v1}" Authorization 'Bearer '
native_auth_descriptor LLM_ANTHROPIC ANTHROPIC_API_KEY "${ANTHROPIC_BASE_URL:-https://api.anthropic.com/v1}" x-api-key ''
native_auth_descriptor SEARCH_TAVILY TAVILY_API_KEY "${TAVILY_BASE_URL:-https://api.tavily.com}" Authorization 'Bearer '
unset -f native_auth_descriptor

# Native leaf tools have explicit paths; no interpreter environment is initialized.
export PATH="${HOME:-}/.local/bin:$PATH"
if [[ -z "${METTACLAW_XMLLINT_BIN:-}" ]]; then
    if command -v xmllint >/dev/null 2>&1; then
        METTACLAW_XMLLINT_BIN="$(command -v xmllint)"
    elif [[ -x "${HOME:-}/miniforge3/bin/xmllint" ]]; then
        METTACLAW_XMLLINT_BIN="${HOME:-}/miniforge3/bin/xmllint"
    fi
    export METTACLAW_XMLLINT_BIN
fi
