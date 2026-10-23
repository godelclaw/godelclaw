; helper.balance_parentheses, native on CeTTa.
;
; src/command_pipeline.metta imports this module only on the CeTTa engine.
; The Python original in src/helper.py stays the SWI-PeTTa path and the
; oracle tests/test_balance_parentheses.py compares this against.
;
; The model's raw response becomes the command batch a turn reads:
;   - text with no (form) whose first word names no skill is prose: ();
;   - one balanced form, or text with no form, is wrapped as ((core)) with its
;     own outer parentheses taken off, when that reads as exactly one form;
;   - otherwise every complete command of every complete top-level form is
;     kept, in order, text outside them and a ")" with nothing open dropped,
;     provided nothing was left open: an unclosed string or form fails the
;     batch, which is then left as ((core)) for the reader to refuse.
; A form is read as helper._top_level_forms reads it: a string runs from " to
; an unescaped ", a backslash in a string takes the next character with it,
; and a ")" with nothing open is skipped. helper._commands_in takes a form
; opening with "(" for a list of commands. Offsets are bytes; the four
; characters that steer the reading are ASCII, so they never fall inside a
; UTF-8 sequence. Whitespace is Python's (str.strip, str.split), from the
; generated src/undelivered_prose_unicode.metta.
;
; The steering pattern, the bare-command vocabulary (skills_help's names, read
; once) and the whitespace classes are src/undelivered_prose.metta's.

!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library system))
!(import! &self ./src/helper.py)
!(import! &self ./src/undelivered_prose.metta)

!(bind! &balance-lead
   (new-state (re:compile (str:concat "\\A" (str:concat (prose:pattern space) "*")))))
!(bind! &balance-trail
   (new-state (re:compile (str:concat (prose:pattern space) "*\\z"))))
!(bind! &balance-blank
   (new-state (re:compile (str:concat "\\A" (str:concat (prose:pattern space) "*\\z")))))
!(bind! &balance-open (new-state (re:compile "\\A\\x{28}*")))
!(bind! &balance-close (new-state (re:compile "\\x{29}*\\z")))

; What helper.balance_parentheses is handed decides what it reads, as for
; prose:undelivered: a string, or a symbol's spelling (a Python str comes back
; from py-call as a symbol), or "true"/"false" for a Bool. A Python str naming
; a function the program defines (a lone "rest", say) comes back as that
; function's grounded atom, which Python is handed as its name. Anything else,
; and text that is not UTF-8, goes to helper.balance_parentheses itself, so it
; answers or raises exactly as before.
(= (balance:parentheses $raw)
   (case (get-metatype $raw)
     ((Symbol (balance:text (str:symbol->string $raw) $raw))
      (Grounded
        (case (get-type $raw)
          ((String (balance:text $raw $raw))
           (Bool (balance:text (if $raw "true" "false") $raw))
           (Number (balance:python $raw))
           ($other (case (str:symbol->string $raw)
                     (((Error $call $why) (balance:python $raw))
                      ($name (balance:text $name $raw))))))))
      ($other (balance:python $raw)))))

(= (balance:python $raw) (py-call (helper.balance_parentheses $raw)))

(= (balance:text $text $raw)
   (case (str:codepoint-count $text)
     (((Error $call $why) (balance:python $raw))
      ($count (balance:returned (balance:of-text $text) $raw)))))

; What py-call makes of the returned str: its symbol. Text holding NUL makes
; no symbol, and py-call hands back what precedes the NUL; Python answers it.
(= (balance:returned $text $raw)
   (case (str:string->symbol $text)
     (((Error $call $why) (balance:python $raw))
      ($symbol $symbol))))

; Python's s.strip(): the text between its first and last non-space character.
(= (balance:strip $text)
   (case (re:find (get-state &balance-lead) $text)
     (((re:match $at $from $groups)
       (if (== $from (str:byte-length $text)) ""
         (case (re:find (get-state &balance-trail) $text)
           (((re:match $to $end $others) (str:byte-slice $text $from $to)))))))))

(= (balance:of-text $raw)
   (let $s (balance:strip $raw)
     (case (balance:forms $s)
       (((balance:read $clean $forms)
         (let $opens (str:starts-with $s "(")
           (if (and (balance:none? $forms)
                    (and (not $opens) (not (prose:bare-command? $s))))
             "()"
             (let $normalized (balance:normalized $s)
               (if (and (or $opens (balance:none? $forms))
                        (balance:one-form? $normalized))
                 $normalized
                 (if (not $clean) $normalized
                   (case (balance:walk (balance:level $forms (balance:done)) $s () 0)
                     (((balance:commands $count $commands)
                       (if (== $count 0) $normalized
                         (balance:recovered $count $commands)))))))))))))))

; Without the outer parentheses the model wrote around it, as
; ((core)): the leading "(" and trailing ")" runs are taken off, then spaces.
(= (balance:normalized $s)
   (let* (($length (str:byte-length $s))
          ($left (balance:run-end (re:find (get-state &balance-open) $s)))
          ($right (- $length (balance:run-start (re:find (get-state &balance-close) $s))))
          ($core (balance:strip (str:byte-slice $s $left (- $length $right)))))
     (str:concat "((" (str:concat $core "))"))))

(= (balance:run-end (re:match $at $end $groups)) $end)
(= (balance:run-start (re:match $at $end $groups)) $at)

; True when text is exactly one balanced parenthesized form.
(= (balance:one-form? $text)
   (case (balance:forms $text)
     (((balance:read True (balance:form (balance:node 0 $end $first $inside) (balance:no-form)))
       (== $end (str:byte-length $text)))
      ($other False))))

(= (balance:none? (balance:no-form)) True)
(= (balance:none? (balance:form $node $rest)) False)

; (balance:read CLEAN FORMS): the complete top-level forms of the text, last
; first, and whether nothing was left open. A form is
; (balance:node START END FIRST INSIDE): where it begins and ends, where its
; first complete form inside begins (-1 for none), and the complete forms
; inside it, last first. The forms inside a form are the top-level forms of
; the text between its parentheses, which is how helper._commands_in reads
; them; reading them once here, in the text's own offsets, takes one pass
; however deeply forms nest.
(= (balance:forms $text)
   (case (re:find-all (get-state &prose-steering) $text)
     (((re:matches $found)
       (balance:scan $text $found 0 False -1
                     (balance:frame -1 -1 (balance:no-form) (balance:top)))))))

; DEPTH of open parentheses, QUOTED inside a string, SKIP the offset a
; backslash escapes, STACK the open forms, innermost first, ending in the
; frame of the text itself: (balance:frame START FIRST INSIDE UP).
(= (balance:scan $text LNil $depth $quoted $skip $stack)
   (balance:read (and (== $depth 0) (not $quoted)) (balance:outermost $stack)))
(= (balance:scan $text (LCons (re:match $at $end $groups) $rest)
                 $depth $quoted $skip $stack)
   (if (== $at $skip)
     (balance:scan $text $rest $depth $quoted -1 $stack)
     (let $c (str:byte-slice $text $at $end)
       (if $quoted
         (if (== $c "\\")
           (balance:scan $text $rest $depth True $end $stack)
           (balance:scan $text $rest $depth (not (== $c "\"")) -1 $stack))
         (if (== $c "\"")
           (balance:scan $text $rest $depth True -1 $stack)
           (if (== $c "(")
             (balance:scan $text $rest (+ $depth 1) False -1
                           (balance:frame $at -1 (balance:no-form) $stack))
             (if (and (== $c ")") (> $depth 0))
               (balance:scan $text $rest (- $depth 1) False -1
                             (balance:closed $end $stack))
               (balance:scan $text $rest $depth False -1 $stack))))))))

; The innermost open form is complete: it is added to the form around it.
(= (balance:closed $end
     (balance:frame $start $first $inside (balance:frame $upstart $upfirst $upinside $up)))
   (balance:frame $upstart (if (== $upfirst -1) $start $upfirst)
                  (balance:form (balance:node $start $end $first $inside) $upinside) $up))

(= (balance:outermost (balance:frame $start $first $inside (balance:top))) $inside)
(= (balance:outermost (balance:frame $start $first $inside (balance:frame $a $b $c $up)))
   (balance:outermost (balance:frame $a $b $c $up)))

; The commands of the forms, which come last first so that each is put in
; front of those after it: (balance:commands COUNT COMMANDS) is the commands,
; in order, as an expression of strings. LEVELS is what is left to read:
; (balance:level FORMS LEVELS), the forms of a level being read before the
; levels above it. A command is (head ...); a form opening with "(" is a list
; of them.
(= (balance:walk (balance:done) $text $commands $count)
   (balance:commands $count $commands))
(= (balance:walk (balance:level (balance:no-form) $up) $text $commands $count)
   (balance:walk $up $text $commands $count))
(= (balance:walk (balance:level (balance:form (balance:node $start $end $first $inside) $rest) $up)
                 $text $commands $count)
   (case (balance:opening $text $start $end $first)
     ((empty (balance:walk (balance:level $rest $up) $text $commands $count))
      (list (balance:walk (balance:level $inside (balance:level $rest $up))
                          $text $commands $count))
      (command (balance:walk (balance:level $rest $up) $text
                             (cons-atom (str:byte-slice $text $start $end) $commands)
                             (+ $count 1))))))

; What is between a form's parentheses: nothing (empty), a form first (list),
; or something else (command). Only the text before the first form inside it,
; or all of it when there is none, need be looked at.
(= (balance:opening $text $start $end $first)
   (if (== $first -1)
     (if (balance:blank? (str:byte-slice $text (+ $start 1) (- $end 1))) empty command)
     (if (or (== $first (+ $start 1))
             (balance:blank? (str:byte-slice $text (+ $start 1) $first)))
       list command)))

(= (balance:blank? $text)
   (re:is-match (get-state &balance-blank) $text))

; The batch made of the commands that were found. The diagnostic Python prints
; to stderr is written there too; it is not part of the value.
(= (balance:recovered $count $commands)
   (let $_ (system:write stderr
             (str:concat "[helper] recovered "
               (str:concat (str:number->string $count)
                 " command(s) from a malformed batch\n")))
     (str:concat "(" (str:concat (str:join " " $commands) ")"))))
