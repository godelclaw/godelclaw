; The engine flags a child session runs with. A coding session shares the
; account's ledger, so it takes the same value allowance run.sh gives the
; agent and its ledger: a stored answer or command output is one value, and
; with the engine's 1 MiB default a large answer cannot reach the ledger and
; waits in local storage for good.
(= (session:engine-flags) ("--value-bytes" "268435456"))
