; Unit tests of src/commentary_contract_cetta.metta; no Python. The driver
; runs this file with METTACLAW_MODEL_COMMENTARY=1 and again with 0.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/commentary_contract_cetta.metta)

(= (unit:size $items) (if (== $items ()) 0 (+ 1 (unit:size (cdr-atom $items)))))
(= (unit:count $text $needle) (- (unit:size (str:split $needle $text)) 1))
(= (old) (commentary:legacy-output-clause))
(= (quoted) (str:concat "\nUSER: quoted evidence: " (old)))
!(if (commentary:enabled?)
   (let* (($rendered (commentary:prompt (str:concat "context " (old))))
          ($_ (nt:require legacy-clause-replaced (not (nt:contains $rendered (old)))))
          ($_ (nt:require tool-instruction-appended (nt:contains $rendered "use the advertised `metta` tool exactly once")))
          ($_ (nt:require private-reasoning-excluded (nt:contains $rendered "never private reasoning")))
          ($_ (nt:require clause-rewritten (nt:contains $rendered "advertised metta tool for the command S-expression")))
          ($twice (commentary:prompt (str:concat (old) (quoted))))
          ($_ (nt:require later-quotation-kept (nt:equal (unit:count $twice (old)) 1)))
          ($_ (nt:require quotation-bytes-kept (nt:contains $twice (quoted))))
          ($batch (commentary:rewrite (str:concat "PROMPT: Output a ((skillName args) ...) S-expression. USER: "
                                                  "Output a ((skillName args) ...) S-expression.")))
          ($_ (nt:require batch-clause-first-only
                (nt:equal (unit:count $batch (commentary:legacy-batch-clause)) 1)))
          ($_ (nt:require batch-clause-rewritten (nt:contains $batch "its program field is the complete")))
          ($_ (nt:require output-tail-required (nt:contains (commentary:output-tail) "REQUIRED_COMMAND_OUTPUT")))
          ; SHA-256 of the texts the Python module carried before this port.
          ($_ (nt:require instruction-pinned
                (nt:equal (str:sha256 (commentary:instruction))
                          "20cfd86da1fcbc61b03ebb9e3a9e961c08a9e140ff14a398914a171dcb1cb8c0")))
          ($_ (nt:require output-instruction-pinned
                (nt:equal (str:sha256 (commentary:output-instruction))
                          "8dba9c53f2a7e3a1e4d09782382d107718ed078e7f07c2f94af89f0a9dc1259e")))
          ($_ (nt:require tool-schema-pinned
                (nt:equal (str:sha256 (commentary:tool-schema-text))
                          "c783079de3789effb218bbe3b20902eade2fcadc76c7a8bd1a052c99020b827b"))))
     (println! UNIT_COMMENTARY_ON_OK))
   (let* (($_ (nt:require disabled-prompt-identity (nt:equal (commentary:prompt "legacy λ") "legacy λ")))
          ($_ (nt:require disabled-rewrite-identity (nt:equal (commentary:rewrite (old)) (old))))
          ($_ (nt:require disabled-tails-empty (nt:equal ((commentary:instruction-tail) (commentary:output-tail)) ("" "")))))
     (println! UNIT_COMMENTARY_OFF_OK)))
