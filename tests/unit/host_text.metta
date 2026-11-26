; host:text, the boundary from host values to text: a String passes through, a
; Symbol becomes its name, and anything else is refused with an Error value.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/interop_flags_cetta.metta)

!(let* (($_ (nt:require a-string-passes-through (== (host:text "λ \"quoted\"\nchat") "λ \"quoted\"\nchat")))
         ($_ (nt:require a-symbol-becomes-its-name (== (host:text chat) "chat")))
         ($_ (nt:require text-is-a-string (== (get-type (host:text chat)) String)))
         ($_ (nt:require a-number-is-refused (== (host:text 123) (Error host:text expected-text))))
         ($_ (nt:require an-expression-is-refused (== (host:text (data value)) (Error host:text expected-text)))))
   (println! UNIT_HOST_TEXT_OK))
