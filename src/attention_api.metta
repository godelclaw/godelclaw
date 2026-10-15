; SPDX-License-Identifier: MIT
; The attention component's public API; help, prompt lines and arities derive
; from these facts. Settings retain their existing readers and persistence.
!(import! &self ./src/api_registry.metta)

(CommandSpec 1 "attention" "attention-policy" 0 2 "(attention-policy [\"key\" [value]])" "Inspect or change persistent ECAN settings; every value, unit and domain is in the live catalogue. CeTTa only." "attention")
(CommandSpec 1 "attention" "attention-use" 1 2 "(attention-use \"id\" [amount])" "Stimulate and consolidate an existing memory; amount defaults to 1. Zero is valid. Optional feedback." "")
(CommandSpec 1 "attention" "attention-link" 3 3 "(attention-link \"from-id\" \"to-id\" weight)" "Replace a directed association's nonnegative weight; zero removes it. Association is separate from factual support." "")
(CommandSpec 1 "attention" "attention-focus" 0 1 "(attention-focus [count])" "Inspect STI/MTI/LTI importance without stimulus; default 20, zero lists all." "")
(CommandSpec 1 "attention" "attention-last" 0 0 "(attention-last)" "Explain the last query's similarity, importance, spreading and combined rank; no memory text." "")
(CommandSpec 1 "attention" "attention-reload" 0 0 "(attention-reload)" "Reload your attention snapshot after editing or repairing it; no restart required." "")

(SettingSpec 1 "attention" "mode" "hybrid" "selector" (Choice ("similarity" "attention" "hybrid")) "persistent" "Default query selector; each query may override it." attention-policy attention-policy)
(SettingSpec 1 "attention" "similarity-weight" 0.8 "fraction" (Interval 0 1) "persistent" "Final hybrid rank: this fraction similarity, the remainder propagated attention." attention-policy attention-policy)
(SettingSpec 1 "attention" "seed-similarity-weight" 0.5 "fraction" (Interval 0 1) "persistent" "Query seeds: mix normalized similarity with stored importance. Zero uses importance; one uses similarity. If all seeds are zero, use uniform mass over the candidate pool." attention-policy attention-policy)
(SettingSpec 1 "attention" "exposure" 0.1 "STI per returned hit" Nonnegative "persistent" "Weak exposure stimulus; not evidence of usefulness. Zero disables it." attention-policy attention-policy)
(SettingSpec 1 "attention" "read-stimulus" 1 "STI per explicit read" Nonnegative "persistent" "Stimulus and consolidation on memory-read. Zero disables this signal." attention-policy attention-policy)
(SettingSpec 1 "attention" "co-use-weight" 1 "link weight per turn" Nonnegative "persistent" "Learn a bidirectional link once for each pair explicitly used in the same model turn; zero disables learning." attention-policy attention-policy)
(SettingSpec 1 "attention" "sti-weight" 1 "importance multiplier" Nonnegative "persistent" "STI contribution to stored importance." attention-policy attention-policy)
(SettingSpec 1 "attention" "mti-weight" 0.5 "importance multiplier" Nonnegative "persistent" "MTI contribution to stored importance." attention-policy attention-policy)
(SettingSpec 1 "attention" "lti-weight" 0.25 "importance multiplier" Nonnegative "persistent" "LTI contribution to stored importance." attention-policy attention-policy)
(SettingSpec 1 "attention" "candidate-multiplier" 3 "times requested count" (IntegerFrom 1) "persistent" "Size of each initial similarity/importance pool. Query count zero still selects all records." attention-policy attention-policy)
(SettingSpec 1 "attention" "spread-rounds" 2 "rounds" (IntegerFrom 0) "persistent" "Synchronous spreading rounds. Zero uses seeds; no upper policy limit or convergence claim." attention-policy attention-policy)
(SettingSpec 1 "attention" "restart" 0.5 "fraction per round" (Interval 0 1) "persistent" "Mass returned to query seeds on each round. Zero is a pure finite walk; one retains the seeds." attention-policy attention-policy)
