; helper.undelivered_prose, native on CeTTa.
;
; src/command_pipeline.metta imports this module only on the CeTTa engine.
; The Python original in src/helper.py stays the SWI-PeTTa path and the
; oracle tests/test_undelivered_prose.py compares this against.
;
; The text no command carries is what is left of the response once each
; complete top-level (form) is replaced by a space, in order and at its first
; occurrence, as str.replace(form, " ", 1) does; then the affect trace and
; every ")" go, and whitespace is normalized line by line.  Forms are read as
; helper._top_level_forms reads them: a string runs from " to an unescaped ",
; a backslash in a string takes the next character with it, and a ")" with
; nothing open is skipped.  Offsets are bytes; the four characters that steer
; the reading are ASCII, so they never fall inside a UTF-8 sequence.
; Whitespace, line boundaries and letters/digits are Python's (str.split,
; str.splitlines, str.isalnum), from the generated
; src/undelivered_prose_unicode.metta.

!(import! &self (library str))
!(import! &self (library re))
!(import! &self ./src/helper.py)
!(import! &self ./src/skills_help.py)
!(import! &self ./src/undelivered_prose_unicode.metta)

!(bind! &prose-steering (new-state (re:compile "[\\\\\"()]")))
!(bind! &prose-line-break (new-state (re:compile (prose:pattern line-break))))
!(bind! &prose-blank-run (new-state (re:compile (prose:pattern blank-run))))
!(bind! &prose-line-edge (new-state (re:compile " ?\\n ?")))
!(bind! &prose-blank-lines (new-state (re:compile "\\n{3,}")))
!(bind! &prose-word (new-state (re:compile (prose:pattern word))))
!(bind! &prose-alnum (new-state (re:compile (prose:pattern alnum))))
!(bind! &prose-affect (new-state (re:compile (prose:pattern affect-trace))))
; The names helper._is_bare_command accepts, owned by skills_help and read
; once. ARITY is settled when skills_help is imported and does not change.
!(bind! &prose-skills
   (new-state (str:set (str:split "\n" (py-call (skills_help.command_names_text))))))
; A Python str comes back from py-call as the symbol it spells, so the loop's
; (repr (UNDELIVERED: $prose ...)) writes the prose unquoted. Prose is
; returned the same way; "" is the empty symbol py-call returns for it, which
; str:string->symbol does not make.
!(bind! &prose-none (new-state (py-call (helper.undelivered_prose ""))))

; What Python is handed for the response decides what it reads: a string,
; or a symbol's spelling (a Python str comes back from py-call as a symbol),
; or "true"/"false" for a Bool. A number is no text. Anything else, and text
; that is not UTF-8, goes to helper.undelivered_prose itself, so it answers
; or raises exactly as before.
(= (prose:undelivered $response)
   (case (get-metatype $response)
     ((Symbol (prose:text (str:symbol->string $response) $response))
      (Grounded
        (case (get-type $response)
          ((String (prose:text $response $response))
           (Number "")
           (Bool (prose:text (if $response "true" "false") $response))
           ($other (prose:python $response)))))
      ($other (prose:python $response)))))

(= (prose:python $response) (py-call (helper.undelivered_prose $response)))

(= (prose:text $text $response)
   (case (str:codepoint-count $text)
     (((Error $call $why) (prose:python $response))
      ($count (prose:returned (prose:of-text $text) $response)))))

; What py-call makes of the prose: its symbol (True and False read as Bool,
; as from Python). Prose holding NUL makes no symbol; Python answers that.
(= (prose:returned $prose $response)
   (if (== $prose "") (get-state &prose-none)
     (case (str:string->symbol $prose)
       (((Error $call $why) (prose:python $response))
        ($symbol $symbol)))))

; No form and a first word that names a skill: a command written without
; its parentheses, which runs, so nothing is undelivered.
(= (prose:of-text $text)
   (case (prose:forms-removed $text)
     (((prose:outside $outside 0)
       (if (prose:bare-command? $text) "" (prose:finish $outside)))
      ((prose:outside $outside $forms) (prose:finish $outside)))))

(= (prose:bare-command? $text)
   (case (re:find (get-state &prose-word) $text)
     (((re:match $at $end $groups)
       (let $word (str:byte-slice $text $at $end)
         (if (str:starts-with $word "(") False
           (str:is-set-member (get-state &prose-skills) $word))))
      ($none False))))

; (prose:outside TEXT COUNT): the text with each of its COUNT complete
; top-level forms replaced, as found, by a space at its first occurrence.
(= (prose:forms-removed $text)
   (case (re:find-all (get-state &prose-steering) $text)
     (((re:matches $found) (prose:scan $text $found 0 0 False -1 $text 0)))))

; DEPTH of open parentheses, START of the open form, QUOTED inside a string,
; SKIP the offset a backslash escapes.
(= (prose:scan $text LNil $depth $start $quoted $skip $outside $forms)
   (prose:outside $outside $forms))
(= (prose:scan $text (LCons (re:match $at $end $groups) $rest)
               $depth $start $quoted $skip $outside $forms)
   (if (== $at $skip)
     (prose:scan $text $rest $depth $start $quoted -1 $outside $forms)
     (let $c (str:byte-slice $text $at $end)
       (if $quoted
         (if (== $c "\\")
           (prose:scan $text $rest $depth $start True $end $outside $forms)
           (prose:scan $text $rest $depth $start (not (== $c "\"")) -1 $outside $forms))
         (if (== $c "\"")
           (prose:scan $text $rest $depth $start True -1 $outside $forms)
           (if (== $c "(")
             (prose:scan $text $rest (+ $depth 1) (if (== $depth 0) $at $start)
                         False -1 $outside $forms)
             (if (and (== $c ")") (== $depth 1))
               (let $form (str:byte-slice $text $start $end)
                 (prose:scan $text $rest 0 $start False -1
                             (prose:replace-first $outside $form) (+ $forms 1)))
               (prose:scan $text $rest (if (and (== $c ")") (> $depth 0)) (- $depth 1) $depth)
                           $start False -1 $outside $forms))))))))

(= (prose:replace-first $text $needle)
   (case (str:find $text $needle)
     (((TextNotFound $missing) $text)
      ($at (str:concat (str:concat (str:byte-slice $text 0 $at) " ")
             (str:byte-slice $text (+ $at (str:byte-length $needle)) (str:byte-length $text)))))))

; The affect trace and every ")" become spaces; each line's whitespace runs
; become one space and its ends none; three or more line breaks become two;
; the ends are stripped. Prose without a letter or digit is none.
(= (prose:finish $outside)
   (let* (($traceless (re:replace-literal (get-state &prose-affect) $outside " "))
          ($unclosed (str:join " " (str:split ")" $traceless)))
          ($lines (re:replace-literal (get-state &prose-line-break) $unclosed "\n"))
          ($spaced (re:replace-literal (get-state &prose-blank-run) $lines " "))
          ($trimmed (re:replace-literal (get-state &prose-line-edge) $spaced "\n"))
          ($folded (re:replace-literal (get-state &prose-blank-lines) $trimmed "\n\n"))
          ($prose (str:trim $folded)))
     (if (re:is-match (get-state &prose-alnum) $prose) $prose "")))
