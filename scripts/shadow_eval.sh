#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
trap 'exit 2' ERR

usage() {
    cat <<'EOF'
Usage: run.sh --shadow-eval --baseline RELEASE --candidate RELEASE --engine BUNDLE
       [--baseline-engine BUNDLE] [--candidate-engine BUNDLE]
       [--suite DRIVER.metta] [--input FILE] [--pairs N] [--output NEW_DIRECTORY]

Compare two runtime releases with the same CeTTa driver and input. The baseline
engine may be Python-enabled so a native port can be compared directly against
the Python-host release it replaced; the candidate engine must stay Python-free.
Each trial has its own Linux filesystem and network namespace and fresh writable
state. Reports differences in observations, outputs, effects, wire requests
(tokens and cache layout), and timing.

Exit 0: all observations/sections agree and checks pass.
Exit 1: a behavioral difference or failed check.
Exit 2: a run or artifact is incomplete.
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
for dependency in awk bwrap jq realpath sha256sum tar timeout; do
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
    local kind=$1 directory=$2 destination=$3 manifest=$4 name hash from rel_dir
    [[ -f $manifest && ! -L $manifest ]] || fail "missing $kind manifest: $manifest"
    jq -es '
      length == 1 and (.[0] | .schema == 1 and (.files | type == "object" and length > 0 and
        all(to_entries[]; (.key|test("^[A-Za-z0-9_./ @+%,=-]+$")) and
            (.value == null or (.value|type == "string" and test("^[a-f0-9]{64}$"))))))
    ' "$manifest" >/dev/null || fail "invalid $kind manifest"
    mkdir -- "$destination"
    jq -r '.files | to_entries[] | select(.value != null) |
       [.value, .key] | @tsv' "$manifest" > "$inputs/manifest-paths"
    : > "$inputs/stage-list"
    : > "$inputs/stage-sums"
    while IFS=$'\t' read -r hash name; do
        case "/$name/" in *//*|*/../*|*/./*) fail "unsafe artifact path: $name" ;; esac
        if [[ $kind == application && $name != config/secrets.env.example ]]; then
            case "$name" in
                .env|.env.*|config/local*|config/secrets*|memory/*|.git/*)
                    fail "instance data in application selection: $name" ;;
            esac
        fi
        from="$directory/$name"
        [[ -f $from && ! -L $from ]] || fail "artifact path is not a regular owned path: $name"
        printf '%s\n' "$name" >> "$inputs/stage-list"
        printf '%s  %s\n' "$hash" "$name" >> "$inputs/stage-sums"
    done < "$inputs/manifest-paths"
    while IFS= read -r rel_dir; do
        [[ -z $rel_dir || $rel_dir == . ]] && continue
        [[ ! -L "$directory/$rel_dir" && -d "$directory/$rel_dir" ]] ||
            fail "artifact path is not a regular owned path: $rel_dir"
    done < <(awk -F/ 'NF>1 {p=""; for(i=1;i<NF;i++){p=(p==""?$i:p"/"$i); if(!seen[p]++) print p}}' "$inputs/stage-list")
    (cd -- "$directory" && tar -cf - -T "$inputs/stage-list") | (cd -- "$destination" && tar -xf -) ||
        fail "failed staging $kind files"
    (cd -- "$destination" && sha256sum -c --quiet "$inputs/stage-sums") >/dev/null 2>&1 ||
        fail "artifact hash differs"
}

baseline_python_enabled=false
baseline_python_prefix=

stage_side() {
    local label=$1 release sdk app_manifest engine_manifest py_flag py_prefix
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
    jq -e '(.revision|type == "string" and length > 0) and (.python_enabled|type == "boolean")' \
        "$engine_manifest" >/dev/null || fail "$label engine manifest is missing revision or python_enabled"
    py_flag=$(jq -r '.python_enabled' "$engine_manifest")
    if [[ $label == candidate ]]; then
        [[ $py_flag == false ]] || fail "candidate needs a pinned Python-disabled CeTTa bundle"
    elif [[ $py_flag == true ]]; then
        py_prefix=$(jq -r '.python_prefix // ""' "$engine_manifest")
        [[ -n $py_prefix && $py_prefix == /* ]] || fail "baseline Python-enabled bundle is missing an absolute python_prefix"
        py_prefix=$(realpath -e -- "$py_prefix") || fail "baseline python_prefix does not exist: $py_prefix"
        [[ -d $py_prefix && -x $py_prefix/bin/python3 && -d $py_prefix/lib ]] ||
            fail "baseline python_prefix is incomplete: $py_prefix"
        baseline_python_enabled=true
        baseline_python_prefix=$py_prefix
    fi
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
         python_enabled:$sdk[0].python_enabled,
         python_version:($sdk[0].python_version // null),
         release_manifest_sha256:$release_manifest_sha256,
         engine_manifest_sha256:$engine_manifest_sha256}' > "$output/$label.json"
}

stage_side baseline "$baseline" "$baseline_engine"
stage_side candidate "$candidate" "$candidate_engine"

sample() {
    local trial=$1 label=$2 dir="$output/$1/$2" rc=0 elapsed= rss=
    local -a input_mount=() python_mount=()
    if [[ -n $input ]]; then
        input_mount=(--ro-bind "$inputs/input" /replay/input --setenv SHADOW_INPUT /replay/input)
    fi
    if [[ $label == baseline && $baseline_python_enabled == true ]]; then
        python_mount=(
            --ro-bind "$baseline_python_prefix" /python
            --setenv PATH /python/bin:/usr/bin:/bin
            --setenv LD_LIBRARY_PATH /python/lib
            --setenv PYTHONHOME /python
            --setenv PYTHONNOUSERSITE 1
            --setenv PYTHONPATH /app/src:/app/modes:/app/modes/iter:/app/channels
            --setenv SHADOW_PYTHON_ENABLED 1
            --setenv SHADOW_PYTHON_PREFIX /python
        )
    else
        python_mount=(
            --setenv PATH /usr/bin:/bin
            --setenv SHADOW_PYTHON_ENABLED 0
        )
    fi
    mkdir -p -- "$dir/work/home"
    # Each trial sees only its staged app and engine (plus read-only /python on a
    # Python-enabled baseline only), the shared driver, read-only input, and its
    # own writable /work directory. The network namespace and loopback are private.
    LC_ALL=C /usr/bin/time -o "$dir/time.txt" -f '%e %M' \
      timeout -k 2 120 \
      bwrap --unshare-all --unshare-user --disable-userns --die-with-parent --new-session \
        --clearenv --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/sbin /sbin \
        --symlink usr/lib /lib --symlink usr/lib64 /lib64 --proc /proc --dev /dev \
        --tmpfs /tmp --dir /run \
        --ro-bind "$inputs/$label-app" /app --ro-bind "$inputs/$label-sdk" /sdk \
        --ro-bind "$inputs/driver.metta" /replay/driver.metta \
        --bind "$dir/work" /work --chdir /app \
        --setenv HOME /work/home --setenv LANG C.UTF-8 \
        --setenv CETTA_ROOT /sdk --setenv CETTA_STDLIB /sdk/lib \
        --setenv SHADOW_SIDE "$label" \
        --setenv SHADOW_REPORT /work/observations.json \
        "${python_mount[@]}" \
        "${input_mount[@]}" -- \
        /sdk/cetta --no-results --lang petta --profile extended --import-mode ancestor-walk /replay/driver.metta \
        </dev/null > "$dir/stdout.log" 2> "$dir/stderr.log" || rc=$?
    local observed="$dir/work/observations.json" status=incomplete
    if [[ $rc == 0 && -f $observed && ! -L $observed ]] &&
        jq -es 'length == 1 and (.[0] | .schema == 1 and .complete == true and
          (.checks_passed|type == "boolean") and
          (.observations|type == "array" and length > 0 and all(.[]; type == "string")) and
          ((.metrics // {})|type == "object") and
          ((.outputs // null)|type == "null" or type == "array" or type == "object") and
          ((.effects // null)|type == "null" or type == "array" or type == "object") and
          ((.wire_requests // null)|type == "null" or type == "array" or type == "object"))' \
          "$observed" >/dev/null; then
        read -r elapsed rss < "$dir/time.txt"
        jq -cS .observations "$observed" > "$dir/observations.json"
        jq -cS '.outputs // null' "$observed" > "$dir/outputs.json"
        jq -cS '.effects // null' "$observed" > "$dir/effects.json"
        jq -cS '.wire_requests // null' "$observed" > "$dir/wire_requests.json"
        status=$(jq -r 'if .checks_passed then "observed" else "failed-checks" end' "$observed")
    fi
    local metrics='{}' outputs_json='null' effects_json='null' wire_json='null'
    if [[ $status != incomplete ]]; then
        metrics=$(jq -c '.metrics // {}' "$observed")
        outputs_json=$(cat "$dir/outputs.json")
        effects_json=$(cat "$dir/effects.json")
        wire_json=$(cat "$dir/wire_requests.json")
    fi
    jq -n --arg status "$status" --argjson exit_code "$rc" --arg elapsed "$elapsed" --arg rss "$rss" \
      --argjson metrics "$metrics" --argjson outputs "$outputs_json" \
      --argjson effects "$effects_json" --argjson wire_requests "$wire_json" \
      '{status:$status,exit:$exit_code,elapsed_seconds:($elapsed|try tonumber catch null),
        peak_process_rss_kib:($rss|try tonumber catch null),metrics:$metrics}
       + (if $outputs != null then {outputs:$outputs} else {} end)
       + (if $effects != null then {effects:$effects} else {} end)
       + (if $wire_requests != null then {wire_requests:$wire_requests} else {} end)' > "$dir/result.json"
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
        jq -n --argjson pair "$pair" --arg status "$status" --arg order "$order" \
            --slurpfile a "$directory/baseline/result.json" --slurpfile b "$directory/candidate/result.json" \
            '{pair:$pair,status:$status,order:$order,baseline:$a[0],candidate:$b[0],differences:null}' \
            > "$directory/result.json"
        printf 'Pair %s (%s): %s\n' "$pair" "$order" "$status" >&2
        continue
    fi
    obs_diff=false out_diff=false eff_diff=false wire_diff=false
    cmp -s -- "$directory/baseline/observations.json" "$directory/candidate/observations.json" || obs_diff=true
    cmp -s -- "$directory/baseline/outputs.json" "$directory/candidate/outputs.json" || out_diff=true
    cmp -s -- "$directory/baseline/effects.json" "$directory/candidate/effects.json" || eff_diff=true
    cmp -s -- "$directory/baseline/wire_requests.json" "$directory/candidate/wire_requests.json" || wire_diff=true
    if [[ $obs_diff == true || $out_diff == true || $eff_diff == true || $wire_diff == true ]]; then
        status=different
        ((result == 2)) || result=1
    elif [[ $a != observed || $b != observed ]]; then
        status=failed-checks
        ((result == 2)) || result=1
    else
        status=match
    fi
    jq -n --argjson pair "$pair" --arg status "$status" --arg order "$order" \
        --argjson obs_diff "$obs_diff" --argjson out_diff "$out_diff" \
        --argjson eff_diff "$eff_diff" --argjson wire_diff "$wire_diff" \
        --slurpfile a "$directory/baseline/result.json" --slurpfile b "$directory/candidate/result.json" \
        --slurpfile a_out "$directory/baseline/outputs.json" --slurpfile b_out "$directory/candidate/outputs.json" \
        --slurpfile a_eff "$directory/baseline/effects.json" --slurpfile b_eff "$directory/candidate/effects.json" \
        --slurpfile a_wire "$directory/baseline/wire_requests.json" --slurpfile b_wire "$directory/candidate/wire_requests.json" \
        '
        def num_delta($x; $y): if ($x != null and $y != null) then (($y - $x) * 1000 | round / 1000) else null end;
        def num_ratio($x; $y): if ($x != null and $y != null and $x > 0) then (($y / $x) * 1000 | round / 1000) else null end;
        def wire_summary($aw; $bw):
          if (($aw|type) == "array" and ($bw|type) == "array") then
            {
              baseline_count: ($aw | length),
              candidate_count: ($bw | length),
              probes: [
                range(0; ([$aw|length, $bw|length] | max)) as $i |
                ($aw[$i] // null) as $x | ($bw[$i] // null) as $y |
                if $x != $y then {
                  index: $i,
                  name: (($x.name // $y.name) // null),
                  protocol: (($x.protocol // $y.protocol) // null),
                  model: (($x.model // $y.model) // null),
                  tokens_changed: (($x.estimated_tokens // null) != ($y.estimated_tokens // null) or ($x.prompt_bytes // null) != ($y.prompt_bytes // null) or ($x.max_tokens // null) != ($y.max_tokens // null)),
                  cache_layout_changed: (($x.cache_layout // null) != ($y.cache_layout // null)),
                  baseline: $x,
                  candidate: $y
                } else empty end
              ]
            }
          else {baseline: $aw, candidate: $bw} end;
        ([
          (if $obs_diff then "observations" else empty end),
          (if $out_diff then "outputs" else empty end),
          (if $eff_diff then "effects" else empty end),
          (if $wire_diff then "wire_requests" else empty end)
        ]) as $cats |
        {
          pair: $pair,
          status: $status,
          order: $order,
          baseline: $a[0],
          candidate: $b[0],
          differences: {
            observations: $obs_diff,
            outputs: $out_diff,
            effects: $eff_diff,
            wire_requests: $wire_diff,
            changed_categories: $cats,
            details: (
              (if $out_diff then {outputs: {baseline: $a_out[0], candidate: $b_out[0]}} else {} end) +
              (if $eff_diff then {effects: {baseline: $a_eff[0], candidate: $b_eff[0]}} else {} end) +
              (if $wire_diff then {wire_requests: wire_summary($a_wire[0]; $b_wire[0])} else {} end)
            ),
            timing: {
              baseline_elapsed_seconds: $a[0].elapsed_seconds,
              candidate_elapsed_seconds: $b[0].elapsed_seconds,
              elapsed_delta_seconds: num_delta($a[0].elapsed_seconds; $b[0].elapsed_seconds),
              elapsed_ratio: num_ratio($a[0].elapsed_seconds; $b[0].elapsed_seconds),
              baseline_peak_rss_kib: $a[0].peak_process_rss_kib,
              candidate_peak_rss_kib: $b[0].peak_process_rss_kib,
              rss_delta_kib: num_delta($a[0].peak_process_rss_kib; $b[0].peak_process_rss_kib)
            }
          }
        }' > "$directory/result.json"
    cats_text=$(jq -r '.differences.changed_categories | join(",")' "$directory/result.json")
    timing_text=$(jq -r '"baseline=\(.differences.timing.baseline_elapsed_seconds)s candidate=\(.differences.timing.candidate_elapsed_seconds)s delta=\(.differences.timing.elapsed_delta_seconds)s"' "$directory/result.json")
    if [[ -n $cats_text ]]; then
        printf 'Pair %s (%s): %s [%s] (%s)\n' "$pair" "$order" "$status" "$cats_text" "$timing_text" >&2
    else
        printf 'Pair %s (%s): %s (%s)\n' "$pair" "$order" "$status" "$timing_text" >&2
    fi
done
input_hash=
if [[ -n $input ]]; then input_hash=$(digest "$inputs/input"); fi
jq -s --slurpfile baseline "$output/baseline.json" --slurpfile candidate "$output/candidate.json" \
    --arg driver_sha256 "$(digest "$inputs/driver.metta")" --arg input_sha256 "$input_hash" \
    --argjson exit_code "$result" \
    '
    def median($arr):
      ($arr | map(select(. != null)) | sort) as $s |
      if ($s | length) == 0 then null else $s[(($s | length) / 2 | floor)] end;
    (sort_by(.pair)) as $ps |
    ([ $ps[] | .differences.changed_categories[]? ] | unique) as $changed |
    (median([ $ps[] | .differences.timing.baseline_elapsed_seconds? ])) as $b_med |
    (median([ $ps[] | .differences.timing.candidate_elapsed_seconds? ])) as $c_med |
    (median([ $ps[] | .differences.timing.elapsed_delta_seconds? ])) as $d_med |
    (median([ $ps[] | .differences.timing.baseline_peak_rss_kib? ])) as $br_med |
    (median([ $ps[] | .differences.timing.candidate_peak_rss_kib? ])) as $cr_med |
    (median([ $ps[] | .differences.timing.rss_delta_kib? ])) as $dr_med |
    {
      schema: 1,
      exit: $exit_code,
      baseline: $baseline[0],
      candidate: $candidate[0],
      driver_sha256: $driver_sha256,
      input_sha256: (if $input_sha256 == "" then null else $input_sha256 end),
      summary: {
        changed_categories: $changed,
        timing: {
          baseline_median_elapsed_seconds: $b_med,
          candidate_median_elapsed_seconds: $c_med,
          median_elapsed_delta_seconds: $d_med,
          median_elapsed_ratio: (if ($b_med != null and $c_med != null and $b_med > 0) then (($c_med / $b_med) * 1000 | round / 1000) else null end),
          baseline_median_peak_rss_kib: $br_med,
          candidate_median_peak_rss_kib: $cr_med,
          median_rss_delta_kib: $dr_med
        }
      },
      pairs: $ps
    }' \
    "$output"/[0-9]*/result.json > "$output/report.json"
printf 'Report: %s/report.json\n' "$output"
exit "$result"
