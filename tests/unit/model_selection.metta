; Unit tests of src/model_selection_cetta.metta; no Python. The driver sets
; SYNTHETIC_MODEL=unit-configured-model and a scratch METTACLAW_MODEL_STATE_PATH.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/model_selection_cetta.metta)

!(nt:require no-choice-recorded (nt:equal (model:choice-in "(other-atom 1)\n(pins x)\n") ()))
!(nt:require first-choice-wins
   (nt:equal (model:choice-in "(other x)\n  (active-model kimi:k3.1-pro)\n(active-model second)\n") "kimi:k3.1-pro"))
!(nt:require unicode-name-exact (nt:equal (model:choice-in "(active-model λ-model)") "λ-model"))
!(nt:require whitespace-may-span-lines (nt:equal (model:choice-in "(active-model\n  syn:large:text )") "syn:large:text"))
!(nt:require two-words-are-no-choice (nt:equal (model:choice-in "(active-model a b)") ()))
!(nt:require mid-line-is-no-choice (nt:equal (model:choice-in "x (active-model hidden)") ()))
!(nt:require recorded-beats-configured (nt:equal (model:select "chosen" "configured") "chosen"))
!(nt:require configured-without-record (nt:equal (model:select () "configured") "configured"))
!(nt:require missing-file-is-no-choice (nt:equal (model:recorded "/nonexistent/godelclaw-model-state") ()))

!(let $path (model:state-path)
   (let* (($_ (if (fs:exists $path) (fs:remove $path) ()))
          ($_ (nt:require configured-model-when-no-file (nt:equal (model:current syn:large:text) "unit-configured-model")))
          ($_ (fs:write $path "(pin a)\n(active-model claude-opus-5-5)\n"))
          ($_ (nt:require durable-choice-read (nt:equal (model:current syn:large:text) "claude-opus-5-5")))
          ($_ (fs:write $path "(active-model openai:fixture)\n"))
          ($_ (nt:require rewritten-choice-read-again (nt:equal (model:current syn:large:text) "openai:fixture")))
          ($_ (fs:write $path "(pin only)\n")))
     (nt:require removed-choice-falls-back (nt:equal (model:current syn:large:text) "unit-configured-model"))))
!(println! UNIT_MODEL_SELECTION_OK)
