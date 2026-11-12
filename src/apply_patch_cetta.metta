; Codex-format patches for coding sessions. A patch is
;
;     *** Begin Patch
;     *** Add File: path          then lines starting with +
;     *** Delete File: path
;     *** Update File: path       optionally *** Move to: path, then hunks:
;     @@ optional context line
;      context line
;     -removed line
;     +added line
;     *** End of File             optional: the hunk ends at the end of the file
;     *** End Patch
;
; Paths are relative to the workspace or absolute, and no two operations may
; target the same file. Every hunk is checked and every new content is staged
; beside its target before any target changes, so a failure up to that point
; leaves the files as they were. The final renames are not atomic as a group:
; a failure among them is reported with what was and was not applied. A hunk's
; context is matched exactly, then ignoring trailing whitespace, then ignoring
; surrounding whitespace, then also mapping typographic dashes, quotes and
; spaces to ASCII, as Codex matches it.
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library str))
!(import! &self (library system))

;; ------------------------------------------------------------- lists

(= (patch:reverse $items $out)
   (if (== $items ()) $out (patch:reverse (cdr-atom $items) (cons-atom (car-atom $items) $out))))
(= (patch:append $left $right) (patch:reverse (patch:reverse $left ()) $right))
(= (patch:length $items) (if (== $items ()) 0 (+ 1 (patch:length (cdr-atom $items)))))
(= (patch:drop $items $count)
   (if (or (<= $count 0) (== $items ())) $items (patch:drop (cdr-atom $items) (- $count 1))))
(= (patch:take $items $count)
   (if (or (<= $count 0) (== $items ())) ()
     (cons-atom (car-atom $items) (patch:take (cdr-atom $items) (- $count 1)))))
(= (patch:map $normal $items)
   (if (== $items ()) ()
     (cons-atom (patch:normal $normal (car-atom $items)) (patch:map $normal (cdr-atom $items)))))
(= (patch:member $item $items)
   (if (== $items ()) False (if (== (car-atom $items) $item) True (patch:member $item (cdr-atom $items)))))
(= (patch:replace $text $old $new) (str:join $new (str:split $old $text)))
(= (patch:failed? $value)
   (case $value (((fs:failed $reason $operation $path $code $progress $message) True) ($other False))))
(= (patch:why $value)
   (case $value (((fs:failed $reason $operation $path $code $progress $message) (str:join "" ($message ": " $path)))
                 ($other (repr $value)))))

;; ----------------------------------------------------------- parsing

(= (patch:blank? $line) (== (str:strip-python $line) ""))
; and/or evaluate both operands: a guard that reads the head of a possibly
; empty list tests emptiness first, in its own branch.
(= (patch:trim-front $lines)
   (if (== $lines ()) () (if (patch:blank? (car-atom $lines)) (patch:trim-front (cdr-atom $lines)) $lines)))
(= (patch:heads? $lines $prefix) (if (== $lines ()) False (str:starts-with (car-atom $lines) $prefix)))
; The end of an added file's lines, or of an update's hunks.
(= (patch:section-end? $lines) (if (== $lines ()) True (str:starts-with (car-atom $lines) "*** ")))
(= (patch:hunks-end? $lines)
   (if (== $lines ()) True
     (let $row (car-atom $lines) (and (str:starts-with $row "*** ") (!= $row "*** End of File")))))
(= (patch:after $line $prefix) (str:strip-python (str:byte-slice $line (str:byte-length $prefix) (str:byte-length $line))))

; (Parsed operations) or (PatchFailed why). Operations are (PatchAdd path
; lines), (PatchDelete path) and (PatchUpdate path move hunks), move being
; NoMove or a path; a hunk is (Hunk context lines eof).
(= (patch:parse $text)
   (let $lines (patch:reverse (patch:trim-front (patch:reverse (patch:trim-front
                 (str:split "\n" (patch:replace $text "\r\n" "\n"))) ())) ())
     (if (if (== $lines ()) True (!= (str:strip-python (car-atom $lines)) "*** Begin Patch"))
       (PatchFailed "a patch starts with *** Begin Patch")
       (let $reversed (patch:reverse $lines ())
         (if (!= (str:strip-python (car-atom $reversed)) "*** End Patch")
           (PatchFailed "a patch ends with *** End Patch")
           (patch:parse-body (cdr-atom (patch:reverse (cdr-atom $reversed) ())) 1 ()))))))
; Body lines start at line 2 of the patch; number is the next line's number.
(= (patch:parse-body $lines $number $ops)
   (if (== $lines ()) (if (== $ops ()) (PatchFailed "the patch changes nothing") (Parsed (patch:reverse $ops ())))
     (let $line (car-atom $lines)
       (if (str:starts-with $line "*** Add File: ")
         (patch:parse-add (patch:after $line "*** Add File: ") (cdr-atom $lines) (+ $number 1) () $ops)
         (if (str:starts-with $line "*** Delete File: ")
           (patch:parse-body (cdr-atom $lines) (+ $number 1)
             (cons-atom (PatchDelete (patch:after $line "*** Delete File: ")) $ops))
           (if (str:starts-with $line "*** Update File: ")
             (patch:parse-move (patch:after $line "*** Update File: ") (cdr-atom $lines) (+ $number 1) $ops)
             (PatchFailed (format-args "line {}: unexpected {}"
                            ((+ $number 1) (repr (str:char-slice $line 0 (min 80 (str:char-length $line)))))))))))))
(= (patch:parse-add $path $lines $number $added $ops)
   (if (patch:section-end? $lines)
     (patch:parse-body $lines $number (cons-atom (PatchAdd $path (patch:reverse $added ())) $ops))
     (let $row (car-atom $lines)
       (if (str:starts-with $row "+")
         (patch:parse-add $path (cdr-atom $lines) (+ $number 1)
           (cons-atom (str:byte-slice $row 1 (str:byte-length $row)) $added) $ops)
         (PatchFailed (format-args "line {}: an added file's lines start with +" ((+ $number 1))))))))
(= (patch:parse-move $path $lines $number $ops)
   (if (patch:heads? $lines "*** Move to: ")
     (patch:parse-hunks $path (patch:after (car-atom $lines) "*** Move to: ") (cdr-atom $lines) (+ $number 1) () $ops)
     (patch:parse-hunks $path NoMove $lines $number () $ops)))
; Hunks are collected newest first, each with its lines newest first.
(= (patch:parse-hunks $path $move $lines $number $hunks $ops)
   (if (patch:hunks-end? $lines)
     (if (and (== $hunks ()) (== $move NoMove)) (PatchFailed (format-args "update of {} has no hunks" ($path)))
       (patch:parse-body $lines $number (cons-atom (PatchUpdate $path $move (patch:finish-hunks $hunks ())) $ops)))
     (let $row (car-atom $lines)
       (if (str:starts-with $row "@@")
         (patch:parse-hunks $path $move (cdr-atom $lines) (+ $number 1)
           (cons-atom (Hunk (patch:after $row "@@") () False) $hunks) $ops)
         (if (== $row "*** End of File")
           (if (== $hunks ()) (PatchFailed (format-args "line {}: *** End of File outside a hunk" ((+ $number 1))))
             (let (Hunk $context $rows $eof) (car-atom $hunks)
               (patch:parse-hunks $path $move (cdr-atom $lines) (+ $number 1)
                 (cons-atom (Hunk $context $rows True) (cdr-atom $hunks)) $ops)))
           (if (and (!= $row "") (not (or (str:starts-with $row " ") (or (str:starts-with $row "+") (str:starts-with $row "-")))))
             (PatchFailed (format-args "line {}: hunk lines start with space, + or -" ((+ $number 1))))
             (let* (($current (if (== $hunks ()) ((Hunk "" () False)) $hunks))
                    ((Hunk $context $rows $eof) (car-atom $current)))
               (patch:parse-hunks $path $move (cdr-atom $lines) (+ $number 1)
                 (cons-atom (Hunk $context (cons-atom (if (== $row "") " " $row) $rows) $eof) (cdr-atom $current)) $ops))))))))
(= (patch:finish-hunks $hunks $out)
   (if (== $hunks ()) $out
     (let (Hunk $context $rows $eof) (car-atom $hunks)
       (patch:finish-hunks (cdr-atom $hunks) (cons-atom (Hunk $context (patch:reverse $rows ()) $eof) $out)))))

;; ---------------------------------------------------------- matching

; Codex's loosest pass maps dashes and minus to "-", single and double quotes
; to ASCII quotes, and non-breaking and typographic spaces to a space.
(= (patch:typographic $text)
   (patch:map-chars $text (("‐" "-") ("‑" "-") ("‒" "-") ("–" "-") ("—" "-") ("―" "-") ("−" "-") ("‘" "'") ("’" "'") ("‚" "'") ("‛" "'") ("“" "\"") ("”" "\"") ("„" "\"") ("‟" "\"") (" " " ") (" " " ") (" " " ") (" " " ") (" " " ") (" " " ") (" " " ") (" " " ") (" " " ") (" " " ") (" " " ") (" " " ") ("　" " "))))
(= (patch:map-chars $text $pairs)
   (if (== $pairs ()) $text
     (let ($from $to) (car-atom $pairs) (patch:map-chars (patch:replace $text $from $to) (cdr-atom $pairs)))))
(= (patch:rstrip $text)
   (let $core (str:strip-python $text)
     (if (== $core "") ""
       (case (str:find $text $core)
         (((TextNotFound $missing) $text)
          ($at (str:byte-slice $text 0 (+ $at (str:byte-length $core)))))))))
(= (patch:normal exact $line) $line)
(= (patch:normal right $line) (patch:rstrip $line))
(= (patch:normal both $line) (str:strip-python $line))
(= (patch:normal typographic $line) (patch:typographic (str:strip-python $line)))

; The index where block occurs at or after start, trying looser matches; an
; end-of-file hunk only matches at the end. None when it occurs nowhere.
(= (patch:find $lines $block $start $eof)
   (if (== $block ()) (if $eof (patch:length $lines) $start)
     (patch:find-passes $lines $block $start $eof (exact right both typographic))))
(= (patch:find-passes $lines $block $start $eof $passes)
   (if (== $passes ()) None
     (let* (($normal (car-atom $passes))
            ($wanted (patch:map $normal $block))
            ($text (patch:map $normal $lines))
            ($size (patch:length $wanted)) ($last (- (patch:length $text) $size))
            ($found (if $eof
                      (if (and (>= $last $start) (patch:prefix? (patch:drop $text $last) $wanted)) $last None)
                      (patch:scan (patch:drop $text $start) $wanted $start $last))))
       (if (== $found None) (patch:find-passes $lines $block $start $eof (cdr-atom $passes)) $found))))
(= (patch:scan $text $wanted $at $last)
   (if (> $at $last) None
     (if (patch:prefix? $text $wanted) $at (patch:scan (cdr-atom $text) $wanted (+ $at 1) $last))))
(= (patch:prefix? $text $wanted)
   (if (== $wanted ()) True
     (if (== $text ()) False
       (if (== (car-atom $text) (car-atom $wanted)) (patch:prefix? (cdr-atom $text) (cdr-atom $wanted)) False))))

;; ---------------------------------------------------------- updating

; Updated text, or (PatchFailed why). Context lines keep the file's own text
; when the match was loose.
(= (patch:updated $name $text $hunks)
   (let* (($trailing (str:ends-with $text "\n"))
          ($all (str:split "\n" $text))
          ($lines (if $trailing (patch:take $all (- (patch:length $all) 1)) $all)))
     (case (patch:hunks $name $lines $hunks 0 1)
       (((PatchFailed $why) (PatchFailed $why))
        ((Patched $result)
         (str:concat (str:join "\n" $result) (if (or $trailing (== $result ())) "\n" "")))))))
(= (patch:hunks $name $lines $hunks $position $number)
   (if (== $hunks ()) (Patched $lines)
     (let (Hunk $context $rows $eof) (car-atom $hunks)
       (let $anchor (if (== $context "") $position (patch:find $lines ($context) $position False))
         (if (== $anchor None)
           (PatchFailed (format-args "{} hunk {}: context {} not found" ($name $number (repr $context))))
           (let* (($old (patch:old-rows $rows)) ($at (patch:find $lines $old $anchor $eof)))
             (if (== $at None)
               (PatchFailed (format-args "{} hunk {}: the lines to change were not found" ($name $number)))
               (let* (($new (patch:new-rows $rows (patch:drop $lines $at)))
                      ($spliced (patch:append (patch:take $lines $at) (patch:append $new (patch:drop $lines (+ $at (patch:length $old)))))))
                 (patch:hunks $name $spliced (cdr-atom $hunks) (+ $at (patch:length $new)) (+ $number 1))))))))))
(= (patch:body $row) (str:byte-slice $row 1 (str:byte-length $row)))
(= (patch:old-rows $rows)
   (if (== $rows ()) ()
     (let $row (car-atom $rows)
       (if (str:starts-with $row "+") (patch:old-rows (cdr-atom $rows))
         (cons-atom (patch:body $row) (patch:old-rows (cdr-atom $rows)))))))
(= (patch:new-rows $rows $original)
   (if (== $rows ()) ()
     (let $row (car-atom $rows)
       (if (str:starts-with $row " ") (cons-atom (car-atom $original) (patch:new-rows (cdr-atom $rows) (cdr-atom $original)))
         (if (str:starts-with $row "-") (patch:new-rows (cdr-atom $rows) (cdr-atom $original))
           (cons-atom (patch:body $row) (patch:new-rows (cdr-atom $rows) $original)))))))

;; ---------------------------------------------------------- planning

(= (patch:resolve $base $name)
   (let $expanded (system:expanduser $name)
     (if (str:starts-with $expanded "/") $expanded (path:join $base $name))))
; Two names for one file are one target: an existing path by its canonical
; name, a new one by its lexically normalised name.
(= (patch:key $path)
   (case (fs:try-canonical-path $path) (((FS:CanonicalPath $real) $real) ($fault (path:normalize $path)))))
(= (patch:mode $path)
   (case (fs:metadata $path follow-permissions)
     (((fs:info $kind $size $identity (fs:permissions $bits)) $bits) ($other None))))
(= (patch:utf8 $path)
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $text) (if (== (str:utf8-error $text) StrUtf8WellFormedV1) (Text $text) (PatchFailed (str:concat $path " is not UTF-8 text"))))
      ($other (PatchFailed (format-args "cannot read {}: {}" ($path (repr $other))))))))

; Planned steps are (Write target text name mode) and (Remove target name);
; mode is the permission bits to give the new content, or None for a new file.
(= (patch:plan $base $ops $steps $receipt)
   (if (== $ops ()) (Planned (patch:reverse $steps ()) (patch:reverse $receipt ()))
     (case (car-atom $ops)
       (((PatchAdd $name $lines)
         (let $target (patch:resolve $base $name)
           (if (fs:exists $target) (PatchFailed (str:concat $name " already exists"))
             (patch:plan $base (cdr-atom $ops)
               (cons-atom (Write $target (str:concat (str:join "\n" $lines) "\n") $name None) $steps)
               (cons-atom (str:concat "A " $name) $receipt)))))
        ((PatchDelete $name)
         (let $target (patch:resolve $base $name)
           (if (not (fs:is-file $target)) (PatchFailed (str:concat $name " does not exist"))
             (patch:plan $base (cdr-atom $ops) (cons-atom (Remove $target $name) $steps)
               (cons-atom (str:concat "D " $name) $receipt)))))
        ((PatchUpdate $name $move $hunks)
         (let $target (patch:resolve $base $name)
           (if (not (fs:is-file $target)) (PatchFailed (str:concat $name " does not exist"))
             (case (patch:utf8 $target)
               (((PatchFailed $why) (PatchFailed $why))
                ((Text $text)
                 (case (patch:updated $name $text $hunks)
                   (((PatchFailed $why) (PatchFailed $why))
                    ($new
                     (if (== $move NoMove)
                       (patch:plan $base (cdr-atom $ops)
                         (cons-atom (Write $target $new $name (patch:mode $target)) $steps)
                         (cons-atom (str:concat "M " $name) $receipt))
                       (patch:plan $base (cdr-atom $ops)
                         (cons-atom (Remove $target $name)
                           (cons-atom (Write (patch:resolve $base $move) $new $move (patch:mode $target)) $steps))
                         (cons-atom (str:join "" ("M " $name " -> " $move)) $receipt))))))))))))))))
(= (patch:step-key $step)
   (case $step (((Write $target $text $name $mode) ($target $name)) ((Remove $target $name) ($target $name)))))
(= (patch:distinct $steps $seen)
   (if (== $steps ()) True
     (let ($target $name) (patch:step-key (car-atom $steps))
       (let $key (patch:key $target)
         (if (patch:member $key $seen) (PatchFailed (str:concat "multiple operations target " $name))
           (patch:distinct (cdr-atom $steps) (cons-atom $key $seen)))))))

;; ----------------------------------------------------- staging, commit

; Create the missing parents of a path, outermost first; (Made dirs) lists the
; ones created, newest first, or (PatchFailed why).
(= (patch:parents $path $made)
   (let $parent (path:dirname $path)
     (if (or (fs:exists $parent) (== $parent $path)) (Made $made)
       (case (patch:parents $parent $made)
         (((Made $more)
           (let $result (fs:make-directories $parent 511)
             (if (patch:failed? $result) (PatchFailed (patch:why $result)) (Made (cons-atom $parent $more)))))
          ($failed $failed))))))
; Stage one step: (Staged temporary) beside its target, Unstaged for a
; removal, or (PatchFailed why). New directories are added to made.
(= (patch:stage $step)
   (case $step
     (((Remove $target $name) (StageDone Unstaged ()))
      ((Write $target $text $name $mode)
       (case (patch:parents $target ())
         (((Made $made)
           (let* (($temporary (path:join (path:dirname $target)
                                (str:join "" ("." (patch:basename $target) "." (str:char-slice (system:uuid4-hex) 0 24)))))
                  ($writer (fs:open-write $temporary ("create" "exclusive") 438)))
             (if (patch:failed? $writer) (StageFailed (patch:why $writer) $made)
               (let* (($wrote (fs:write-all $writer $text)) ($_ (fs:close $writer))
                      ($moded (if (or (patch:failed? $wrote) (== $mode None)) $wrote (fs:set-mode $temporary $mode))))
                 (if (or (patch:failed? $wrote) (patch:failed? $moded))
                   (let $_ (fs:remove $temporary) (StageFailed (patch:why (if (patch:failed? $wrote) $wrote $moded)) $made))
                   (StageDone (Staged $temporary) $made))))))
          ((PatchFailed $why) (StageFailed $why ()))))))))
(= (patch:basename $path) (patch:last (str:split "/" $path)))
(= (patch:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (patch:last (cdr-atom $items))))
(= (patch:stage-all $steps $staged $made)
   (if (== $steps ()) (AllStaged (patch:reverse $staged ()) $made)
     (case (patch:stage (car-atom $steps))
       (((StageDone $one $created) (patch:stage-all (cdr-atom $steps) (cons-atom $one $staged) (patch:append $created $made)))
        ((StageFailed $why $created)
         (let $_ (patch:discard $staged (patch:append $created $made)) (PatchFailed $why)))))))
(= (patch:discard $staged $made)
   (let* (($_ (patch:remove-staged $staged)) ($_ (patch:remove-dirs $made))) True))
(= (patch:remove-staged $staged)
   (if (== $staged ()) True
     (let $_ (case (car-atom $staged) (((Staged $temporary) (if (fs:exists $temporary) (fs:remove $temporary) ())) ($other ())))
       (patch:remove-staged (cdr-atom $staged)))))
; Created directories, newest first: rmdir only empties.
(= (patch:remove-dirs $made)
   (if (== $made ()) True
     (let $_ (proc:run ("rmdir" "--" (car-atom $made)) "/" inherit 5000 0 "merged") (patch:remove-dirs (cdr-atom $made)))))
(= (patch:names $steps)
   (if (== $steps ()) ()
     (let ($target $name) (patch:step-key (car-atom $steps)) (cons-atom $name (patch:names (cdr-atom $steps))))))
(= (patch:commit $steps $staged $done)
   (if (== $steps ()) Committed
     (let* (($step (car-atom $steps))
            ($result (case $step
                       (((Write $target $text $name $mode)
                         (case (car-atom $staged) (((Staged $temporary) (fs:rename $temporary $target)) ($other $other))))
                        ((Remove $target $name) (fs:remove $target))))))
       (if (patch:failed? $result)
         (let* (($_ (patch:remove-staged $staged))
                ($rest (patch:names $steps)))
           (str:join "" ("Patch partly applied: " (patch:why $result) "\nApplied: "
                         (if (== $done ()) "nothing" (str:join ", " (patch:reverse $done ())))
                         "\nNot applied: " (str:join ", " $rest))))
         (let ($target $name) (patch:step-key $step)
           (patch:commit (cdr-atom $steps) (cdr-atom $staged) (cons-atom $name $done)))))))

; Apply the patch and answer a receipt: "Patch failed: reason" means nothing
; was written; "Patch partly applied" names what was and was not applied.
(= (patch:apply $patch $workdir)
   (if (== (system:getenv-or-default "METTACLAW_CODING_CHECK" "") "1")
     "Patch failed: patches are refused while a checkpoint is checked"
     (case (patch:apply-checked $patch (system:expanduser $workdir))
       (((PatchFailed $why) (str:concat "Patch failed: " $why))
        ($receipt $receipt)))))
(= (patch:apply-checked $patch $base)
   (case (patch:parse $patch)
     (((PatchFailed $why) (PatchFailed $why))
      ((Parsed $ops)
       (case (patch:plan $base $ops () ())
         (((PatchFailed $why) (PatchFailed $why))
          ((Planned $steps $receipt)
           (case (patch:distinct $steps ())
             (((PatchFailed $why) (PatchFailed $why))
              (True
               (case (patch:stage-all $steps () ())
                 (((PatchFailed $why) (PatchFailed $why))
                  ((AllStaged $staged $made)
                   (case (patch:commit $steps $staged ())
                     ((Committed (str:concat "Success. Updated the following files:\n" (str:join "\n" $receipt)))
                      ($partial $partial))))))))))))))))
