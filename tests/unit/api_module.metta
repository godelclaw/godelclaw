; The component API's module schema: attaching it admits the live attention
; fields and keeps the base core empty, a field of the wrong scalar type is
; refused, and the runtime's later view of the fields is the same set.
!(import! &self ../native_test_support.metta)

; The decimal number that starts at byte $at of $text.
(= (am:number $text $at) (am:digits (str:python-codepoints (str:byte-slice $text $at (str:byte-length $text))) 0))
(= (am:digits $points $n)
   (if (== $points ()) $n
     (let $p (car-atom $points)
       (if (and (>= $p 48) (<= $p 57)) (am:digits (cdr-atom $points) (+ (* $n 10) (- $p 48))) $n))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ($probe (path:join $home "module.metta"))
         ($_ (fs:write $probe (str:join "" (
           "!(import! &self " (json-stringify (JsonString (path:join $repo "src/attention_parameters.metta"))) ")\n"
           "!(import! &self " (json-stringify (JsonString (path:join $repo "src/api_module_cetta.metta"))) ")\n"
           "(= (fixture:count $xs) (case $xs ((LNil 0) ((LCons $head $tail) (+ 1 (fixture:count $tail))))))\n"
           "!(let* (($schema (api-module:schema " (json-stringify (JsonString (path:join $repo "src/api/ComponentAPI.module"))) "))\n"
           "        ($layer (api-module:attach $schema \"attention\"))\n"
           "        ($joined (module:join-layers $layer $layer))\n"
           "        ($fields (module:layer-fields $joined (SyntaxOriginV1 \"attention\" \"public-api-v1\")))\n"
           "        ($base (module:base-core $joined))\n"
           "        ($bad (module:attach-layer $base (SyntaxOriginV1 \"bad\" \"public-api-v1\") $schema \"Field\"\n"
           "          (LCons (CommandField \"bad\" \"bad\" \"zero\" 0 \"(bad)\" \"wrong scalar type\") LNil))))\n"
           "   (println! (MODULE (fixture:count $fields)\n"
           "     (== $base (GSLTLanguageDefWireV1 \"ComponentMetadata\" LNil LNil LNil LNil)) $bad)))\n"
           "!(println! (LATER (size-atom (api:module-fields \"attention\"))))\n"))))
         ($run (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $probe) $repo
                         (nt:environment $home) 30000 1048576))
         ($out (case $run (((proc:exited 0 $o $e) $o) ($bad (repr $bad)))))
         ($_ (nt:require the-probe-runs-without-errors (and (nt:contains $out "(LATER ") (not (nt:contains $out "(Error ")))))
         ; The counts the probe printed: (MODULE n ...) and (LATER n).
         ($module-count (case (str:find $out "(MODULE ") (((TextNotFound $_) -1) ($module-at (am:number $out (+ $module-at 8))))))
         ($later-count (case (str:find $out "(LATER ") (((TextNotFound $_) -2) ($later-at (am:number $out (+ $later-at 7))))))
         ($_ (nt:require the-schema-admits-the-live-fields (> $module-count 0)))
         ($_ (nt:require the-base-core-stays-empty (nt:contains $out (str:concat "(MODULE " (str:concat (repr $module-count) " true ")))))
         ($_ (nt:require a-wrongly-typed-field-is-refused (nt:contains $out "(ModuleRefusedV1 type_mismatch")))
         ($_ (nt:require the-runtime-sees-the-same-fields (== $module-count $later-count))))
   (println! UNIT_API_MODULE_OK))

