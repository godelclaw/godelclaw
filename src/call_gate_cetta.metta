; The gate at a provider call's boundary: no call is submitted while the
; operator has paused cognition, a requested recycle ends the process at this
; boundary, and a coding check refuses the call.
;
; The usual state is decided here, without the host: cognition is enabled and
; no recycle is requested. Every other state belongs to the host gate
; (native_llm_host.gate), which waits, exits or refuses, and keeps the watchdog
; informed while it waits: a pause, a recycle, a coding check, a mode runtime
; with its own gate, or a state that cannot be read. A coding session outlives
; the agent's recycles, so it does not read the recycle request.
!(import! &self (library system))
!(import! &self ./src/lifecycle_cetta.metta)
!(import! &self ./src/boot_state_cetta.metta)

(: call-gate:decide (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (call-gate:decide $check $runtime $session)
   (if (or (== $check "1") (not (== $runtime ""))) host
     (case (catch (lifecycle-native:enabled))
       ((1 (if (== $session "1") clear
             (case (catch (boot-state:recycle)) ((False clear) ($other host)))))
        ($other host)))))
(= (call-gate:state)
   (call-gate:decide (system:getenv-or-default "METTACLAW_CODING_CHECK" "")
                     (system:getenv-or-default "METTACLAW_MODE_RUNTIME" "")
                     (system:getenv-or-default "METTACLAW_CODING_SESSION" "")))
(= (call-gate:pass)
   (if (== (call-gate:state) clear) True (py-call (native_llm_host.gate))))
