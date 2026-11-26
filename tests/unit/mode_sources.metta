; The native mode modules: their forms balance, they cross no Python or Prolog
; boundary, and the lifecycle's retry budget comes from the platform's own
; temporary-name limit rather than a constant.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))
!(import! &self (library re))

(= (ms:names) ("mode_text_cetta" "mode_values_cetta" "mode_io_cetta" "mode_paths_cetta" "engine_modes_cetta"
               "attention_cetta" "fuel_cetta" "loop_modes_cetta" "lifecycle_cetta" "mode_state"))
(= (ms:source $repo $name) (fs:read-text (path:join $repo (str:concat "src/" (str:concat $name ".metta")))))
; Comments and strings set aside, the parentheses that remain.
(= (ms:parens $text $lexer)
   (case (re:find-all $lexer $text)
     (((re:matches $matches) (filter-atom (ms:tokens $matches $text ()) $t (or (== $t "(") (== $t ")"))))
      ($bad (Unlexed $bad)))))
(= (ms:tokens $matches $text $done)
   (case $matches ((LNil (list:reverse $done)) ((LCons $m $rest) (ms:tokens $rest $text (cons-atom (re:group-text $m $text 0) $done))))))
(= (ms:balanced? $parens $depth)
   (if (== $parens ()) (== $depth 0)
     (let $next (if (== (car-atom $parens) "(") (+ $depth 1) (- $depth 1))
       (if (< $next 0) False (ms:balanced? (cdr-atom $parens) $next)))))
(= (ms:code $text $comments) (re:replace-literal $comments $text ""))

!(let* (($repo (nt:required-env "TEST_REPOSITORY"))
         ($lexer (re:compile ";[^\n]*|\"(?:\\\\.|[^\"\\\\])*\"|[()]"))
         ($comments (re:compile ";[^\n]*"))
         ($_ (nt:require the-lexer-sees-the-forms (> (size-atom (ms:parens (ms:source $repo "lifecycle_cetta") $lexer)) 100)))
         ($_ (nt:require every-mode-module-balances
               (== (filter-atom (ms:names) $name (not (ms:balanced? (ms:parens (ms:source $repo $name) $lexer) 0))) ())))
         ($_ (nt:require no-mode-module-crosses-a-foreign-boundary
               (== (filter-atom (ms:names) $name
                     (let $code (ms:code (ms:source $repo $name) $comments)
                       (or (nt:contains $code "(py-call") (nt:contains $code "(import_prolog_function"))))
                   ())))
         ($lifecycle (ms:source $repo "lifecycle_cetta"))
         ($_ (nt:require the-retry-budget-comes-from-the-platform
               (and (nt:contains $lifecycle "(lifecycle-native:create $path $directory $state (system:tmp-max))")
                    (and (not (nt:contains $lifecycle "$state 10000)")) (not (nt:contains $lifecycle "$state 238328)"))))))
         ($_ (re:close $lexer)) ($_ (re:close $comments)))
   (println! UNIT_MODE_SOURCES_OK))
