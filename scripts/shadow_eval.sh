#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
trap 'exit 2' ERR

usage() {
    cat <<'EOF'
Usage: run.sh --shadow-eval --baseline RELEASE --candidate RELEASE --engine BUNDLE
       [--baseline-engine BUNDLE] [--candidate-engine BUNDLE]
       [--suite DRIVER.metta] [--input FILE] [--pairs N] [--output NEW_DIRECTORY]

Compare two runtime releases with the same CeTTa driver and input. The default
driver tests hierarchy writes, historical reads and restart. Each trial has its
own Linux filesystem and network namespace and fresh writable state.

Exit 0: all observations agree and checks pass. Exit 1: a behavioral difference
or failed check. Exit 2: a run or artifact is incomplete.
EOF
}

fail() {
    printf 'shadow-eval: %s\n' "$*" >&2
    exit 2
}

shadow_root=$(cd -- "$(dirname -- "$0")" && pwd)
baseline= candidate= engine= baseline_engine= candidate_engine= input= output=
suite="$shadow_root/shadow_hierarchy.metta"
pairs=1
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --baseline|--candidate|--engine|--baseline-engine|--candidate-engine|--suite|--input|--pairs|--output)
            (($# >= 2)) && [[ -n $2 ]] || fail "missing value for $1"
            case "$1" in
                --baseline) baseline=$2 ;;
                --candidate) candidate=$2 ;;
                --engine) engine=$2 ;;
                --baseline-engine) baseline_engine=$2 ;;
                --candidate-engine) candidate_engine=$2 ;;
                --suite) suite=$2 ;;
                --input) input=$2 ;;
                --pairs) pairs=$2 ;;
                --output) output=$2 ;;
            esac
            shift 2 ;;
        *) fail "unknown option $1" ;;
    esac
done
baseline_engine=${baseline_engine:-$engine}
candidate_engine=${candidate_engine:-$engine}
[[ -n $baseline && -n $candidate && -n $baseline_engine && -n $candidate_engine ]] ||
    fail "supply both releases and their engine bundles (see --help)"
[[ $pairs =~ ^[1-9][0-9]{0,2}$ ]] || fail "--pairs must be an integer from 1 to 999"
for dependency in bwrap jq realpath sha256sum timeout; do
    command -v "$dependency" >/dev/null || fail "missing $dependency"
done
[[ -x /usr/bin/time ]] || fail "missing GNU time at /usr/bin/time"
suite=$(realpath -e -- "$suite") && [[ -f $suite ]] || fail "driver is missing"
if [[ -n $input ]]; then
    input=$(realpath -e -- "$input") && [[ -f $input ]] || fail "input file is missing"
fi

if [[ -z $output ]]; then
    shadow_state=${XDG_STATE_HOME:-$HOME/.local/state}/godelclaw/shadow-eval
    mkdir -p -- "$shadow_state"
    output=$(mktemp -d "$shadow_state/run.XXXXXXXX")
else
    mkdir -- "$output" || fail "output must be a new directory"
fi
output=$(realpath -e -- "$output")
inputs=$(mktemp -d "$output/.inputs.XXXXXXXX")
trap 'rm -rf -- "$inputs"' EXIT
printf 'Shadow evaluation evidence: %s\n' "$output" >&2
cp -- "$suite" "$inputs/driver.metta"
if [[ -n $input ]]; then cp -- "$input" "$inputs/input"; fi

digest() {
    local sum
    sum=$(sha256sum < "$1") || return
    printf '%s\n' "${sum%% *}"
}

# Copy only the manifest's named files. Check the copied bytes, including
# libraries that each trial actually loads, before a sandbox can execute them.
stage() {
    local kind=$1 directory=$2 destination=$3 manifest=$4 name hash from path
    [[ -f $manifest && ! -L $manifest ]] || fail "missing $kind manifest: $manifest"
    jq -es '
      length == 1 and (.[0] | .schema == 1 and (.files | type == "object" and length > 0 and
        all(to_entries[]; (.key|test("^[A-Za-z0-9_./ @+%,=-]+$")) and
            (.value == null or (.value|type == "string" and test("^[a-f0-9]{64}$"))))))
    ' "$manifest" >/dev/null || fail "invalid $kind manifest"
    mkdir -- "$destination"
    jq -r '.files | to_entries[] | select(.value != null) |
       [.value, .key] | @tsv' "$manifest" > "$inputs/manifest-paths"
    while IFS=$'\t' read -r hash name; do
        case "/$name/" in *//*|*/../*|*/./*) fail "unsafe artifact path: $name" ;; esac
        if [[ $kind == application && $name != config/secrets.env.example ]]; then
            case "$name" in
                .env|.env.*|config/local*|config/secrets*|memory/*|.git/*)
                    fail "instance data in application selection: $name" ;;
            esac
        fi
        from="$directory/$name"
        path=$(realpath -e -- "$from") || fail "missing artifact file: $name"
        [[ $path == "$from" && -f $from ]] || fail "artifact path is not a regular owned path: $name"
        mkdir -p -- "$destination/$(dirname -- "$name")"
        cp --preserve=mode --reflink=auto -- "$from" "$destination/$name"
        [[ $(digest "$destination/$name") == "$hash" ]] || fail "artifact hash differs: $name"
    done < "$inputs/manifest-paths"
}

stage_side() {
    local label=$1 release sdk app_manifest engine_manifest
    release=$(realpath -e -- "$2") || fail "$label release is missing"
    sdk=$(realpath -e -- "$3") || fail "$label engine is missing"
    app_manifest="$inputs/$label-app.json"
    engine_manifest="$inputs/$label-engine.json"
    cp --no-dereference -- "$release/manifest.json" "$app_manifest" || fail "$label app manifest is missing"
    cp --no-dereference -- "$sdk/build-manifest.json" "$engine_manifest" || fail "$label engine manifest is missing"
    [[ -f $app_manifest && ! -L $app_manifest && -f $engine_manifest && ! -L $engine_manifest ]] ||
        fail "$label manifests must be regular files"
    jq -e '(.release|type == "string" and test("^[a-f0-9]{64}$"))' \
        "$app_manifest" >/dev/null || fail "$label release identity is missing"
    jq -e '.python_enabled == false and (.revision|type == "string" and length > 0)' \
        "$engine_manifest" >/dev/null || fail "$label needs a pinned Python-disabled CeTTa bundle"
    stage application "$release/files" "$inputs/$label-app" "$app_manifest"
    stage engine "$sdk" "$inputs/$label-sdk" "$engine_manifest"
    cp -- "$engine_manifest" "$inputs/$label-sdk/build-manifest.json"
    [[ -x $inputs/$label-sdk/cetta && -d $inputs/$label-sdk/lib ]] ||
        fail "$label engine bundle is incomplete"
    jq -n --slurpfile app "$app_manifest" --slurpfile sdk "$engine_manifest" \
       --arg release_manifest_sha256 "$(digest "$app_manifest")" \
       --arg engine_manifest_sha256 "$(digest "$engine_manifest")" \
       '{release:$app[0].release, source_commit:$app[0].source_commit,
         engine_revision:$sdk[0].revision, engine_sha256:$sdk[0].files.cetta,
         release_manifest_sha256:$release_manifest_sha256,
         engine_manifest_sha256:$engine_manifest_sha256}' > "$output/$label.json"
}

stage_side baseline "$baseline" "$baseline_engine"
stage_side candidate "$candidate" "$candidate_engine"

sample() {
    local trial=$1 label=$2 dir="$output/$1/$2" rc=0 elapsed= rss=
    local -a input_mount=()
    if [[ -n $input ]]; then
        input_mount=(--ro-bind "$inputs/input" /replay/input --setenv SHADOW_INPUT /replay/input)
    fi
    mkdir -p -- "$dir/work/home"
    # The candidate sees only its staged app and engine, the shared driver,
    # read-only input and its own work directory. The loopback is private.
    LC_ALL=C /usr/bin/time -o "$dir/time.txt" -f '%e %M' \
      timeout -k 2 120 \
      bwrap --unshare-all --unshare-user --disable-userns --die-with-parent --new-session \
        --clearenv --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/sbin /sbin \
        --symlink usr/lib /lib --symlink usr/lib64 /lib64 --proc /proc --dev /dev \
        --tmpfs /tmp --dir /run \
        --ro-bind "$inputs/$label-app" /app --ro-bind "$inputs/$label-sdk" /sdk \
        --ro-bind "$inputs/driver.metta" /replay/driver.metta \
        --bind "$dir/work" /work --chdir /app \
        --setenv PATH /usr/bin:/bin --setenv HOME /work/home --setenv LANG C.UTF-8 \
        --setenv CETTA_ROOT /sdk --setenv CETTA_STDLIB /sdk/lib \
        --setenv SHADOW_REPORT /work/observations.json \
        "${input_mount[@]}" -- \
        /sdk/cetta --no-results --lang petta --profile extended --import-mode ancestor-walk /replay/driver.metta \
        </dev/null > "$dir/stdout.log" 2> "$dir/stderr.log" || rc=$?
    local observed="$dir/work/observations.json" status=incomplete
    if [[ $rc == 0 && -f $observed && ! -L $observed ]] &&
        jq -es 'length == 1 and (.[0] | .schema == 1 and .complete == true and
          (.checks_passed|type == "boolean") and
          (.observations|type == "array" and length > 0 and all(.[]; type == "string")) and
          ((.metrics // {})|type == "object"))' "$observed" >/dev/null; then
        read -r elapsed rss < "$dir/time.txt"
        jq -cS .observations "$observed" > "$dir/observations.json"
        status=$(jq -r 'if .checks_passed then "observed" else "failed-checks" end' "$observed")
    fi
    local metrics='{}'
    if [[ $status != incomplete ]]; then metrics=$(jq -c '.metrics // {}' "$observed"); fi
    jq -n --arg status "$status" --argjson exit_code "$rc" --arg elapsed "$elapsed" --arg rss "$rss" \
      --argjson metrics "$metrics" \
      '{status:$status,exit:$exit_code,elapsed_seconds:($elapsed|try tonumber catch null),
        peak_process_rss_kib:($rss|try tonumber catch null),metrics:$metrics}' > "$dir/result.json"
}

result=0
for ((pair=1; pair<=pairs; pair++)); do
    order=baseline-first
    if ((pair % 2)); then
        sample "$pair" baseline
        sample "$pair" candidate
    else
        order=candidate-first
        sample "$pair" candidate
        sample "$pair" baseline
    fi
    directory="$output/$pair"
    a=$(jq -r .status "$directory/baseline/result.json")
    b=$(jq -r .status "$directory/candidate/result.json")
    if [[ $a == incomplete || $b == incomplete ]]; then
        status=incomplete
        result=2
    elif ! cmp -s -- "$directory/baseline/observations.json" "$directory/candidate/observations.json"; then
        status=different
        ((result == 2)) || result=1
    elif [[ $a != observed || $b != observed ]]; then
        status=failed-checks
        ((result == 2)) || result=1
    else
        status=match
    fi
    jq -n --argjson pair "$pair" --arg status "$status" --arg order "$order" \
        --slurpfile a "$directory/baseline/result.json" --slurpfile b "$directory/candidate/result.json" \
        '{pair:$pair,status:$status,order:$order,baseline:$a[0],candidate:$b[0]}' > "$directory/result.json"
    printf 'Pair %s (%s): %s\n' "$pair" "$order" "$status" >&2
done
input_hash=
if [[ -n $input ]]; then input_hash=$(digest "$inputs/input"); fi
jq -s --slurpfile baseline "$output/baseline.json" --slurpfile candidate "$output/candidate.json" \
    --arg driver_sha256 "$(digest "$inputs/driver.metta")" --arg input_sha256 "$input_hash" \
    --argjson exit_code "$result" \
    '{schema:1,exit:$exit_code,baseline:$baseline[0],candidate:$candidate[0],
      driver_sha256:$driver_sha256,input_sha256:(if $input_sha256=="" then null else $input_sha256 end),
      pairs:sort_by(.pair)}' \
    "$output"/[0-9]*/result.json > "$output/report.json"
printf 'Report: %s/report.json\n' "$output"
exit "$result"
