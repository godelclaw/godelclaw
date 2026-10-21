; Print the read-only retry/wake diagnostics for a provider ledger as JSON.
; Run from the repository root:
;   cetta --lang petta --import-mode ancestor-walk scripts/inspect_retry_wake.metta [LEDGER]
; The ledger defaults to METTACLAW_LLM_LEDGER_PATH. Exit status 1 on any error.
!(import! &self (library lib_import))
!(import! &self (library system))
!(import! &self ../verification/RetryWakeInspect.metta)

(= (inspect:ledger)
   (if (> (system:argc) 0) (system:arg (- (system:argc) 1))
     (system:getenv-or-default "METTACLAW_LLM_LEDGER_PATH" "")))
!(let $ledger (inspect:ledger)
   (if (== $ledger "") (let $_ (println! "inspect: supply a ledger path or METTACLAW_LLM_LEDGER_PATH") (system:exit 1))
     (case (gc:inspect $ledger (system:wall-time))
       (((GcInspection $json) (println! (json-stringify $json)))
        ((GcInspectionError $reason) (let $_ (println! (str:concat "inspect: " $reason)) (system:exit 1)))))))
