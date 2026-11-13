; Unit tests of src/call_gate_cetta.metta; no Python. The gate answers "clear"
; only for the state the host gate would pass at once; every other state is
; the host's, which waits, exits or refuses.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/call_gate_cetta.metta)
; A process whose mode differs from the selected one is asked to recycle.
(= (call-gate-test:recycling?)
   (not (== (system:getenv-or-default "METTACLAW_ACTIVE_LOOP_MODE" "") "")))
; The latch's path, as pathlib builds it: empty and "." components are
; dropped, ".." is kept, and exactly two leading slashes survive.
!(nt:require mode-paths-as-pathlib
   (nt:equal (map-atom ("a//b/./c" "/a/" "//a" "///a" "." "" "/" "a/../b" "./a") $path (mode-path:pathlib $path))
             ("a/b/c" "/a" "//a" "/a" "." "." "/" "a/../b" "a")))
(= (call-gate-test:run)
   (let* (; No latch file: cognition is stopped, as lifecycle._read() reads it.
          ($_ (nt:require missing-latch-is-the-hosts (nt:equal (call-gate:decide "" "" "") host)))
          ($_ (nt:require missing-latch-holds-a-coding-session (nt:equal (call-gate:decide "" "" "1") host)))
          ($_ (lifecycle-native:start))
          ($recycling (call-gate-test:recycling?))
          ($_ (nt:require running-is-clear-unless-recycling
                (nt:equal (call-gate:decide "" "" "") (if $recycling host clear))))
          ($_ (nt:require state-reads-the-environment
                (nt:equal (call-gate:state) (if $recycling host clear))))
          ; A coding session outlives the agent's recycles.
          ($_ (nt:require coding-session-ignores-recycle (nt:equal (call-gate:decide "" "" "1") clear)))
          ($_ (nt:require coding-check-is-the-hosts (nt:equal (call-gate:decide "1" "" "") host)))
          ($_ (nt:require coding-check-in-a-session-is-the-hosts (nt:equal (call-gate:decide "1" "" "1") host)))
          ($_ (nt:require mode-runtime-is-the-hosts (nt:equal (call-gate:decide "" "iter" "") host)))
          ($_ (nt:require other-check-values-do-not-refuse
                (nt:equal (call-gate:decide "0" "" "1") clear)))
          ($_ (lifecycle-native:stop))
          ($_ (nt:require paused-is-the-hosts (nt:equal (call-gate:decide "" "" "") host)))
          ($_ (nt:require paused-holds-a-coding-session (nt:equal (call-gate:decide "" "" "1") host)))
          ; A latch that cannot be read as running never clears the gate.
          ($_ (fs:write (mode-path:lifecycle) "{broken"))
          ($_ (nt:require torn-latch-is-the-hosts (nt:equal (call-gate:decide "" "" "1") host)))
          ($_ (fs:write (mode-path:lifecycle) "{\"schema\": 1, \"state\": \"running\", \"extra\": 1}"))
          ($_ (nt:require foreign-latch-is-the-hosts (nt:equal (call-gate:decide "" "" "1") host))))
     True))
!(nt:require call-gate (call-gate-test:run))
!(println! (if (call-gate-test:recycling?) UNIT_CALL_GATE_RECYCLING_OK UNIT_CALL_GATE_OK))
