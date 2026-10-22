; Native helper.recent_actions / helper.relevant_files. Python is the oracle
; and remains the SWI backend. No journal contents are parsed as MeTTa code.
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library math))
!(import! &self (library system))
!(import! &self ./src/strategy_host.py)
!(import! &self ./src/undelivered_prose_unicode)
!(import! &self ./src/action_window_unicode)

(= (aw:tail-bytes) 262144)
; Python strip includes Unicode whitespace and the four C0 separators.
!(bind! &aw-space (new-state
   (let $word (prose:pattern word)
     (str:concat "[" (str:concat (str:byte-slice $word 2 (- (str:byte-length $word) 2)) "]")))))
!(bind! &aw-trim (new-state (re:compile
   (str:join "" ("^" (get-state &aw-space) "+|" (get-state &aw-space) "+$")))))
!(bind! &aw-lstrip (new-state (re:compile (str:concat "\\A" (str:concat (get-state &aw-space) "+")))))
!(bind! &aw-rstrip (new-state (re:compile (str:concat (get-state &aw-space) "+\\z"))))
!(bind! &aw-spaces (new-state (re:compile (str:concat (get-state &aw-space) "+"))))
!(bind! &aw-parens (new-state (re:compile "[()]")))
!(bind! &aw-integer (new-state (re:compile "\\A[+-]?[0-9](?:_?[0-9])*\\z")))
; Python \w is isalnum or underscore, NOT the regex library's Unicode \w
; (which also admits combining marks and omits some numeric characters).
; L + N is checked against Python for every Unicode scalar in the tests.
!(bind! &aw-future-alnum (new-state (re:compile (aw:future-alnum))))
!(bind! &aw-path (new-state (re:compile
   "(?<![\\p{L}\\p{N}_/.])((?:~/|\\./|/)?(?:[\\p{L}\\p{N}_.@+\\-]+/)+[\\p{L}\\p{N}_.@+\\-]+)")))
!(bind! &aw-path-token (new-state (re:compile "[\\p{L}\\p{N}_.@+~/\\-]+")))

(= (aw:trim $text) (re:replace-literal (get-state &aw-trim) $text ""))
(= (aw:lstrip $text) (re:replace-literal (get-state &aw-lstrip) $text ""))
(= (aw:rstrip $text) (re:replace-literal (get-state &aw-rstrip) $text ""))
(= (aw:drop-last $text) (str:byte-slice $text 0 (- (str:byte-length $text) 1)))
(= (aw:contains $text $part)
   (case (str:find $text $part) (((TextNotFound $_) False) ($at True))))
(= (aw:reverse $items $out)
   (case $items ((LNil $out) ((LCons $head $tail)
     (let $next (LCons $head $out) (aw:reverse $tail $next))))))
(= (aw:strings $items)
   (case $items ((LNil ()) ((LCons $head $tail)
     (let $rest (aw:strings $tail) (cons-atom $head $rest))))))

; int(), not float(): decimal text may contain single underscores, Unicode
; decimal digits, a sign and Python whitespace. Invalid limits are caught by
; the public functions just as the oracle catches ValueError/TypeError.
(= (aw:int $value)
   (case (get-type $value)
     ((Number (if (or (math:isnan $value) (math:isinf $value))
                (Error aw:int nonfinite-number) (math:trunc $value)))
      (Bool (if $value 1 0))
      ($other
       (if (or (== (get-metatype $value) Symbol) (== $other String))
         (let* (($text (aw:trim $value))
                ($digits (aw:ascii-digits $text (aw:decimal-patterns))))
           (if (and (not (re:is-match (re:compile "[\\x{1C}-\\x{1F}]") $value))
                    (re:is-match (get-state &aw-integer) $digits))
             (let $plain (str:join "" (str:split "_" $digits))
               (if (> (str:char-length (re:replace-literal (re:compile "[+-]") $plain "")) 4300)
                 (Error aw:int too-many-digits) (str:string->number $plain)))
             (Error aw:int invalid-integer)))
         (Error aw:int invalid-type))))))
(= (aw:ascii-digits $text $patterns)
   (if (== $patterns ()) $text
     (let* (((($pattern $digit) $rest) (decons-atom $patterns))
            ($next (re:replace-literal (re:compile $pattern) $text $digit)))
       (aw:ascii-digits $next $rest))))

; fs:tail-text seeks before reading and repairs UTF-8 exactly as Python's
; decode(errors="replace"). A block starts on a line beginning `("`; lines
; end at every str.splitlines boundary, CRLF counting once. One native scan
; finds every block start; only the blocks the window reaches, newest first,
; are then split into lines (with the trailing empty piece that a final
; boundary leaves, which no rule below can tell from absence).
; A header match ends with the two ASCII bytes `("`. (The equivalent
; lookbehind form scans quadratically in this engine.)
!(bind! &aw-header (new-state (re:compile
   "(?:\\A|[\\x{A}-\\x{D}\\x{1C}-\\x{1E}\\x{85}\\x{2028}-\\x{2029}])\\(\"")))
!(bind! &aw-line-break (new-state (re:compile (prose:pattern line-break))))
(= (aw:action-blocks $path)
   (case (fs:tail-text $path (aw:tail-bytes))
     (((fs:tail $size $truncated $text)
       (let (re:matches $starts) (re:find-all (get-state &aw-header) $text)
         (AwBlocks $text (aw:reverse $starts LNil) (str:byte-length $text))))
      ($unreadable (AwBlocks "" LNil 0)))))
(= (aw:commands-of $block)
   (case $block ((LNil "") ((LCons $stamp $lines) (aw:find-commands $lines)))))
; appendToHistory writes a turn as one tuple whose "\n" strings become
; literal newlines, so lines after the stamp open with a quote:
; `" ((cmd ...`, `" "HUMAN_MESSAGE: " ...`, `" ERROR_FEEDBACK: ...`.
(= (aw:journal-line $line)
   (let $text (aw:lstrip $line)
     (if (str:starts-with $text "\"")
       (aw:lstrip (str:byte-slice $text 1 (str:byte-length $text))) $text)))
; The response starts at the first line reading `((` once its quote is
; dropped, and ends before ERROR_FEEDBACK: or the closing `")`. Only its
; first line loses the quote; the trailing ` "` is trimmed from the whole.
(= (aw:find-commands $lines)
   (case $lines
     ((LNil "")
      ((LCons $line $rest)
       (let $first (aw:journal-line $line)
         (if (str:starts-with $first "((")
           (let $more (aw:command-lines $rest)
             (aw:unquote (aw:trim (str:join "\n" (cons-atom $first $more)))))
           (aw:find-commands $rest)))))))
(= (aw:command-lines $lines)
   (case $lines
     ((LNil ())
      ((LCons $line $rest)
       (if (str:starts-with (aw:journal-line $line) "ERROR_FEEDBACK:") ()
         (if (== (aw:trim $line) "\")") ()
           (let $more (aw:command-lines $rest) (cons-atom $line $more))))))))
(= (aw:unquote $text)
   (if (and (str:ends-with $text "\"") (not (str:ends-with $text "\\\"")))
     (aw:rstrip (aw:drop-last $text)) $text))
; A stamp line ends with the quote that opens the next "\n".
(= (aw:stamp $line)
   (let $stamp (aw:trim $line)
     (if (str:ends-with $stamp "\" \"") (aw:rstrip (aw:drop-last $stamp)) $stamp)))
; Noise is a turn with nothing but no-op forms and parentheses: drop every
; whitespace character, then each no-op token in the oracle's order.
(= (aw:is-noise $commands)
   (let* (($packed (re:replace-literal (get-state &aw-spaces) $commands ""))
          ($nop (str:join "" (str:split "(nop)" $packed)))
          ($rest (str:join "" (str:split "(no_commands_parsed)" $nop))))
     (== (re:replace-literal (get-state &aw-parens) $rest "") "")))

(= (aw:recent-actions $path) (aw:recent-actions $path 8 12000))
(= (aw:recent-actions $path $limit) (aw:recent-actions $path $limit 12000))
(= (aw:recent-actions $path $limit $max-chars)
   (case (catch (aw:recent-checked $path (aw:int $limit) (aw:int $max-chars)))
     (((Error $call $why) "(proposal window unavailable)") ($text $text))))
(= (aw:recent-checked $path $limit $budget)
   (case ($limit $budget)
     ((((Error $call $why) $_) "(proposal window unavailable)")
      (($_ (Error $call $why)) "(proposal window unavailable)")
      ($valid
       (let $text (aw:path-text $path)
         (if (aw:contains $text (str:from-codepoints (0))) "(proposal window unavailable)"
           (aw:select (aw:action-blocks $text) (max 1 $limit) (max 500 $budget) 0 ())))))))
(= (aw:path-text $path)
   (case (get-type $path)
     ((Bool (if $path "True" "False")) ($other (format-args "{}" ($path))))))
(= (aw:select $blocks $left $budget $used $chosen)
   (let (AwBlocks $text $starts $end) $blocks
     (if (or (== $left 0) (== $starts LNil)) (aw:window $chosen)
       (let* (((LCons (re:match $_ $after $_) $older) $starts)
              ($at (- $after 2))
              ((re:pieces $block) (re:split (get-state &aw-line-break) (str:byte-slice $text $at $end)))
              ($rest (AwBlocks $text $older $at)))
         (aw:select-block $block $rest $left $budget $used $chosen)))))
(= (aw:select-block $block $rest $left $budget $used $chosen)
   (let $commands (aw:commands-of $block)
     (if (aw:is-noise $commands) (aw:select $rest $left $budget $used $chosen)
       (let* (((LCons $stamp $_) $block)
              ($rendered (str:join " " ((aw:stamp $stamp) $commands)))
              ($bounded (if (and (== $chosen ()) (> (str:char-length $rendered) $budget))
                          (str:concat (str:char-slice $rendered 0 (- $budget 1)) "…") $rendered))
              ($cost (+ (str:char-length $bounded) 2)))
         (if (and (!= $chosen ()) (> (+ $used $cost) $budget)) (aw:window $chosen)
           (let $next (cons-atom $bounded $chosen)
             (aw:select $rest (- $left 1) $budget (+ $used $cost) $next)))))))
(= (aw:window $chosen)
   (if (== $chosen ()) "(no prior actions recorded)" (str:join "\n\n" $chosen)))

(= (aw:relevant-files $path) (aw:relevant-files $path 8 25))
(= (aw:relevant-files $path $limit) (aw:relevant-files $path $limit 25))
(= (aw:relevant-files $path $limit $max-paths)
   (case (catch (aw:files-checked (aw:recent-actions $path $limit 60000) (aw:int $max-paths)))
     (((Error $call $why) "(unavailable)") ($text $text))))
(= (aw:files-checked $window $maximum)
   (case $maximum
     (((Error $call $why) "(unavailable)")
      ($n
       (let* (($text (aw:python-alnum-text $window))
              ((re:matches $tokens) (re:find-all (get-state &aw-path-token) $text))
              ($newest (aw:reverse $tokens LNil))
              ($chosen (aw:token-paths $newest $text (max 1 $n) LNil)))
         (if (== $chosen LNil) "(none)"
           (str:join " " (aw:strings (aw:reverse $chosen LNil)))))))))
; The supplied engine has newer Unicode properties than the Python oracle.
; Those extra scalars are non-word delimiters to Python; a space has exactly
; that role here. Match and slice the same transformed text (byte offsets may
; change), never the proposal text returned by recent-actions.
(= (aw:python-alnum-text $text)
   (re:replace-literal (get-state &aw-future-alnum) $text " "))
; No path match can cross a character outside this alphabet. Tokenizing first
; avoids repeatedly searching a large non-path prefix with lookbehind. The
; full oracle expression still controls all matches WITHIN each token.
; Tokens and their matches are visited newest first, so a repeated path
; ranks by its last mention, and the walk stops once $left paths are chosen.
; $seen holds the chosen paths, newest last.
(= (aw:token-paths $tokens $text $left $seen)
   (case $tokens
     ((LNil $seen)
      ((LCons (re:match $start $end $groups) $rest)
       (let $token (str:byte-slice $text $start $end)
         (if (aw:contains $token "/")
           (let* (((re:matches $matches) (re:find-all (get-state &aw-path) $token))
                  ($newest (aw:reverse $matches LNil))
                  ((AwPaths $count $next) (aw:paths $newest $token $left $seen)))
             (if (== $count 0) $next (aw:token-paths $rest $text $count $next)))
           (aw:token-paths $rest $text $left $seen)))))))
(= (aw:paths $matches $text $left $seen)
   (case $matches
     ((LNil (AwPaths $left $seen))
      ((LCons (re:match $start $end $groups) $rest)
       (let $candidate (aw:rstrip-dot (str:byte-slice $text $start $end))
         (if (and (aw:path? $candidate) (not (aw:member $candidate $seen)))
           (let $next (LCons $candidate $seen)
             (if (== $left 1) (AwPaths 0 $next) (aw:paths $rest $text (- $left 1) $next)))
           (aw:paths $rest $text $left $seen)))))))
; Of the oracle's rstrip(".,;:'\"") set only '.' belongs to the regex.
(= (aw:rstrip-dot $text)
   (if (str:ends-with $text ".")
     (aw:rstrip-dot (str:byte-slice $text 0 (- (str:byte-length $text) 1))) $text))
(= (aw:member $item $items)
   (case $items ((LNil False) ((LCons $head $tail)
     (if (== $item $head) True (aw:member $item $tail))))))
(= (aw:prefix? $text $prefixes)
   (if (== $prefixes ()) False
     (if (str:starts-with $text (car-atom $prefixes)) True
       (aw:prefix? $text (cdr-atom $prefixes)))))
(= (aw:path? $candidate)
   (let $parts (str:split "/" $candidate)
     (and (>= (str:char-length $candidate) 6)
       (and (aw:contains $candidate "/")
         (and (not (or (== $candidate "/dev/null") (== $candidate "/tmp")))
           (and (not (aw:prefix? $candidate ("http" "//")))
             (and (<= (size-atom $parts) 13)
               (or (aw:prefix? $candidate ("/" "./" "~/" "src/" "tests/" "memory/" "channels/" "scripts/" "packages/" "lean/" "repos/" "docs/"))
                 (aw:contains (aw:last $parts) ".")))))))))
(= (aw:last $items)
   (if (== (cdr-atom $items) ()) (car-atom $items) (aw:last (cdr-atom $items))))
; py-call returns Python text as an unquoted Symbol in the PeTTa dialect.
; Its spelling truncates at NUL; reproduce kind and rendering at this edge.
; The foreign carrier also retains a hidden Python payload after NUL, which
; no native Symbol constructor can represent. See the explicit expected-
; failure identity test and REPORT.md; ordinary journal text has no such gap.
(= (aw:legacy-atom $text)
   (case (str:find $text (str:from-codepoints (0)))
     (((TextNotFound $_) (str:string->symbol $text))
      ($at (str:string->symbol (str:byte-slice $text 0 $at))))))
; helper.path_from_env without Python. Its value used to cross the bridge as
; a Symbol, so spellings such as True reached the views as "true"; the text
; that Symbol renders to is what the views now open.
(= (action-window:history-path)
   (let $text (system:getenv-or-default "METTACLAW_HISTORY_PATH" "./memory/history.metta")
     (if (== $text "") ""
       (let $atom (str:string->symbol $text) (format-args "{}" ($atom))))))
(= (action-window:recent $path $limit) (aw:legacy-atom (aw:recent-actions $path $limit)))
(= (action-window:files $path $limit) (aw:legacy-atom (aw:relevant-files $path $limit)))

; The two context_sources loaders, computed here with their arguments and
; condition and handed to the bundle as text: the history path as Python
; reads it (raw, no Symbol round trip), helper.int_env's turn count, and the
; recent-proposals view only off the strategy path, where the loader shows
; its TASK_CONTEXT notice instead.
(= (aw:int-env $name $default)
   (let $text (system:getenv-or-default $name "")
     (if (== $text "") $default
       (case (catch (aw:int $text)) (((Error $call $why) $default) ($n $n))))))
(= (action-window:context-views)
   (let* (($history (system:getenv-or-default "METTACLAW_HISTORY_PATH" "./memory/history.metta"))
          ($turns (aw:int-env "METTACLAW_RECENT_ACTION_TURNS" 12))
          ($files (aw:relevant-files $history $turns)))
     (if (== (py-call (strategy_host.enabled_flag)) 1)
       (("relevant-files" $files))
       (let $recent (aw:recent-actions $history $turns)
         (("recent-proposals" $recent) ("relevant-files" $files))))))
