; Execute the neutral coGSLT module through the same PeTTa dialect used by the
; live agent.  The result retains the exact rewrite rule that justified it.
!(import! &self (library lib_import))
!(import! &self (library lib_module))

!(let* (($reader (langdef:load-baked-parser "module-core-reader"))
        ($module (module:read-file $reader "retry-wake"
                   "verification/RetryWake.module"))
        ($closed-reader (langdef:close-parser $reader))
        ($theory (module:assemble $module "RetryWake" LNil LNil
                   OriginViewV1 "godelclaw-retry-wake:v1"))
        ($core (module:core $theory "RetryWake"))
        ($authority (langdef:load-language-value $core))
        ($result (langdef:rewrite-term-at-data $authority
                   "CallState" "CallState" 8 100000
                   (LangDef:RelationRows)
                   (Reconcile (RequestFailed) (CallSubmitted))))
        ($closed (langdef:close-language $authority)))
   (println! (GodelClawModuleApplicationV1
     "88c6713192cb30ed95013746cb627693da467b659d7fd1e9d1599c2df942d638"
     $result)))
