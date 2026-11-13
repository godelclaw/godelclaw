; Native help pages, dry-run verdicts and legacy command argument errors.
; Page text is literal generated data; this module owns lookup and disclosure.
; command_arity.metta owns the shared command vocabulary and its refinement.

!(import! &self (library math))
!(import! &self (library re))
!(import! &self (library str))
!(import! &self ./src/command_arity.metta)
!(import! &self ./src/skills_help_pages.metta)
!(import! &self ./src/small_views_unicode.metta)

!(bind! &help-pages
   (new-state (sort-atom (unique-atom (collapse (match &self
     (HelpPageV1 $name $text) (HelpPageV1 $name $text)))))))
!(bind! &help-nonprintable
   (new-state (re:compile (sv:nonprintable-pattern))))

(= (help:contains $text $part)
   (case (str:find $text $part) (((TextNotFound $_) False) ($at True))))
(= (help:hex $number $width)
   (if (== $width 0) ""
     (let* (($quotient (math:floor (/ $number 16)))
            ($digit (- $number (* $quotient 16))))
       (str:concat (help:hex $quotient (- $width 1))
         (str:char-slice "0123456789abcdef" $digit (+ $digit 1))))))
(= (help:ordinal $char) (car-atom (str:python-codepoints $char)))
(= (help:repr-char $char $quote)
   (case $char
     (("\t" "\\t") ("\n" "\\n") ("\r" "\\r") ("\\" "\\\\")
      ($other (if (== $char $quote) (str:concat "\\" $char)
        (if (re:is-match (get-state &help-nonprintable) $char)
          (let $code (help:ordinal $char)
            (if (< $code 256) (str:concat "\\x" (help:hex $code 2))
              (if (< $code 65536) (str:concat "\\u" (help:hex $code 4))
                (str:concat "\\U" (help:hex $code 8))))) $char))))))
(= (help:repr-string $text)
   (let $quote (if (and (help:contains $text "'")
                        (not (help:contains $text "\""))) "\"" "'")
     (str:join "" ($quote
       (str:join "" (map-atom (str:codepoints $text) $char
                      (help:repr-char $char $quote))) $quote))))

(= (help:page-rows) (get-state &help-pages))
(= (help:find-page $name $rows)
   (if (== $rows ()) ""
     (let (HelpPageV1 $head $text) (car-atom $rows)
       (if (== $head $name) $text (help:find-page $name (cdr-atom $rows))))))
(= (help:page-names)
   (map-atom (help:page-rows) $row
     (let (HelpPageV1 $name $text) $row $name)))
(= (help:index)
   (str:concat "help pages: "
     (str:concat
       (str:join " " (sort-atom (unique-atom
         (union-atom (help:page-names) (arity:names)))))
       " — (help \"name\") for one page")))
(= (help:argument-count $lo $hi)
   (if (== $lo $hi) (str:number->string $lo)
     (str:join "-" ((str:number->string $lo) (str:number->string $hi)))))
(= (help:normalize-name $raw)
   (str:strip-python (str:strip-python $raw) "\""))
(= (help:lookup $raw)
   (let $name (help:normalize-name $raw)
     (if (== $name "") (help:index)
       (let $page (help:find-page $name (help:page-rows))
         (if (!= $page "") $page
           (case (arity:bounds $name)
             (((ArityBoundsV1 $lo $hi)
               (str:join "" ($name " takes " (help:argument-count $lo $hi)
                             " args; no longer page yet.")))
              ($unknown (str:concat "no help for "
                          (str:concat (help:repr-string $name)
                            " — (help) lists everything"))))))))))
(= (help:page $name) (help:lookup $name))
(= (help:command-names-text) (arity:names-text))

(= (help:compact $text)
   (if (<= (str:char-length $text) 90) $text
     (str:concat (str:char-slice $text 0 90) "…")))
(= (help:parse-error? $rendered $low)
   (or (str:starts-with $low "(error")
     (or (help:contains $low "syntax_error")
         (help:contains $low "parse error"))))
(= (help:verdict $original $raw-rendered)
   (let* (($rendered (str:strip-python $raw-rendered))
          ($low (str:python-lower $rendered)))
     (if (help:parse-error? $rendered $low)
       (str:concat "PARSE FAILED: "
         (str:concat (help:compact $rendered)
           " — the loop would reject this batch. Common causes: unbalanced parentheses, an apostrophe or unescaped quote inside a string."))
       (let $tokens (arity:split-top $rendered)
         (if (or (== $tokens arity:not-expression) (== $tokens ()))
           (str:concat "parses, but as a bare atom ("
             (str:concat (help:compact $rendered)
               ") — a command must be (skill args...)"))
           (let* (($head (car-atom $tokens))
                  ($argc (- (size-atom $tokens) 1)))
             (case (arity:bounds $head)
               (((ArityUnknownV1 $name)
                 (str:join "" ("parses as (" $head " … "
                   (str:number->string $argc) " args) but "
                   (help:repr-string $head)
                   " is NOT a known skill — (help) lists them")))
                ((ArityBoundsV1 $lo $hi)
                 (if (and (>= $argc $lo) (<= $argc $hi))
                   (str:join "" ("OK: (" $head " …) with "
                     (str:number->string $argc)
                     " args — valid call, not executed"))
                   (str:join "" ("ARITY: (" $head " …) got "
                     (str:number->string $argc) " args, takes "
                     (help:argument-count $lo $hi)
                     (if (and (> $argc $hi) (== $head "shell"))
                       " — quote the whole command as ONE string" "")
                     " — this is the error the loop reports as a format failure"))))))))))))

(= (help:invalid-command) "COMMAND_ERROR: expected (skill arguments...)")
(= (help:command-symbol $head)
   (case (catch (str:symbol->string $head))
     (((Error $call $why) HelpInvalidCommandNameV1)
      ($name (HelpCommandNameV1 $name)))))
(= (help:command-head $head)
   (case (get-metatype $head)
     ((Symbol (help:command-symbol $head))
      (Grounded (case (get-type $head)
                  ((String (HelpCommandNameV1 $head))
                   (Number HelpInvalidCommandNameV1)
                   (Bool HelpInvalidCommandNameV1)
                   ($other (help:command-symbol $head)))))
      ($other HelpInvalidCommandNameV1))))
(= (help:command-error-named $name $argc)
   (case (arity:bounds $name)
     (((ArityBoundsV1 $lo $hi)
       (if (or (< $argc $lo) (> $argc $hi))
         (str:concat "COMMAND_ERROR: wrong number of arguments. "
           (help:lookup $name)) ""))
      ($unknown ""))))
(= (help:command-error $command)
   (if (or (!= (get-metatype $command) Expression) (== (size-atom $command) 0))
     (help:invalid-command)
     (case (help:command-head (car-atom $command))
       ((HelpInvalidCommandNameV1 (help:invalid-command))
        ((HelpCommandNameV1 $name)
         (help:command-error-named $name (- (size-atom $command) 1)))))))
