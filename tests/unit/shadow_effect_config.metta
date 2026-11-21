; An obsolete or inconsistent shadow selection must never acquire a live effect.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/command_dispatch_core.metta)
!(import! &self ../../src/command_dispatch_hooks.metta)
!(import! &self ../../src/api_registry.metta)
!(bind! &probe-selection (new-state ("" "")))
!(bind! &probe-count (new-state 0))
(CommandSpec 1 "fixture" "probe-shadow-effect" 0 0 "(probe-shadow-effect)" "Count a fixture effect" "")

(= (dispatch:env $name $default)
   (let ($backend $state) (get-state &probe-selection)
     (case $name
       (("METTACLAW_EFFECT_BACKEND" $backend)
        ("METTACLAW_EFFECT_BACKEND_STATE" $state)
        ($key $default)))))
(= (dispatch:length $text) (str:byte-length $text))
(= (dispatch:compare $a $b) (str:compare $a $b))
(= (probe-shadow-effect)
   (let $_ (change-state! &probe-count (+ (get-state &probe-count) 1)) "live"))
(= (probe-shadow-selection $backend $state)
   (let $_ (change-state! &probe-selection ($backend $state))
     (dispatch:effect (probe-shadow-effect))))
(= (probe-shadow-refuses $backend $state $reason)
   (let $result (probe-shadow-selection $backend $state)
     (nt:require shadow-refusal-has-no-live-effect
       (and (nt:contains (repr (quote $result)) $reason) (== (get-state &probe-count) 0)))))
!(progn
  (probe-shadow-refuses "tmux-shadow" "legacy-state" "shadow-state-unavailable")
  (probe-shadow-refuses "tmux_shadow" "legacy-state" "unknown effect backend")
  (probe-shadow-refuses "" "legacy-state" "inconsistent shadow effect configuration")
  (let* (($ordinary (probe-shadow-selection "" "")) ($count (get-state &probe-count)))
    (nt:require ordinary-command-executes-exactly-once
      (and (== $ordinary "live") (== $count 1))))
  (println! UNIT_SHADOW_EFFECT_CONFIG_OK))
