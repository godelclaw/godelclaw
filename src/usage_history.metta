; Read the same identified call events as the operator report. No Python
; bridge, migration, request submission or model-context allocation is needed.
!(import! &self (library sqlite))

; Return sqlite:result rows (call-id, event, observed-time, JSON metadata).
; Calls are selected by admission time; their subsequent observations remain
; attached. Missing ledgers/tables return the ordinary SQLite error.
(= (usage:events $ledger $since $until)
   (sqlite:observe $ledger
     "SELECT h.call_id,h.event,h.observed,h.detail FROM llm_call_history h JOIN llm_call_history a ON a.call_id=h.call_id AND a.event='admitted' WHERE a.observed>=? AND a.observed<? ORDER BY h.sequence"
     ($since $until) 1000))
