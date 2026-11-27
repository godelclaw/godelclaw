; Regenerate the Unicode tables godelclaw matches text with, from the Unicode
; Character Database files that config/unicode-sources-15.json pins.
;
; The tables state Python 3.12's text predicates as Unicode 15.0.0 defines
; them: str.isalnum, str.isprintable, str.isspace, the line breaks
; str.splitlines splits at, unicodedata.decimal, and where str.casefold
; differs from str.lower. A table written as a correction over the regex
; property classes (\p{L}, \p{N}, \p{C}, \p{Z}) is measured scalar by scalar
; against the engine running this program, and must compile to the same
; pattern as its full explicit class before anything is written.
;
;   cetta --lang petta --profile extended scripts/generate_unicode_tables.metta UCD_DIR
;   cetta --lang petta --profile extended scripts/generate_unicode_tables.metta UCD_DIR --check
;
; UCD_DIR holds the pinned files at their published paths under
; https://www.unicode.org/Public/15.0.0/ucd/ (UnicodeData.txt,
; extracted/DerivedNumericType.txt, ...). A file whose size or SHA-256
; differs from its pin is refused (exit 2), as is an engine that is not the
; bundle CETTA_REF selects, and nothing is written. --check writes nothing
; and exits 1 naming each stale table.
;
; Long lists are built by map-atom and filter-atom: consing onto a long
; expression copies it, and deep recursion over one holds every frame.
!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library list))
!(import! &self (library math))
!(import! &self (library path))
!(import! &self (library system))

(= (ut:root) (path:dirname (system:entry-directory)))
(= (ut:at $relative) (path:join (ut:root) $relative))

;; ── Refusal ──────────────────────────────────────────────────────────────

(= (ut:refuse $why)
   (progn (system:write stderr (str:concat "REFUSE: " (str:concat $why "\n"))) (system:exit 2)))
(= (ut:require $ok $why) (if $ok True (ut:refuse $why)))

;; ── Text ─────────────────────────────────────────────────────────────────

(= (ut:hex-digit $c)
   (case $c
     (("0" 0) ("1" 1) ("2" 2) ("3" 3) ("4" 4) ("5" 5) ("6" 6) ("7" 7) ("8" 8) ("9" 9)
      ("A" 10) ("B" 11) ("C" 12) ("D" 13) ("E" 14) ("F" 15)
      ($other (ut:refuse (str:concat "not a hexadecimal digit: " $other))))))
(= (ut:hex-from $text $at $end $acc)
   (if (>= $at $end) $acc
     (ut:hex-from $text (+ $at 1) $end
       (+ (* $acc 16) (ut:hex-digit (str:byte-slice $text $at (+ $at 1)))))))
; The value of upper-case hexadecimal text, such as a UCD code point field.
(= (ut:hex $text) (ut:hex-from $text 0 (str:byte-length $text) 0))
(= (ut:hex-text-of $n $acc)
   (let (IntegerDivmodV1 $q $r) (math:integer-divmod $n 16)
     (let $digit (str:byte-slice "0123456789ABCDEF" $r (+ $r 1))
       (if (== $q 0) (str:concat $digit $acc) (ut:hex-text-of $q (str:concat $digit $acc))))))
; Upper-case hexadecimal without leading zeros, as Python's "%X" writes it.
(= (ut:hex-text $n) (ut:hex-text-of $n ""))
(= (ut:lower-hex4 $n)
   (let* (($digits (str:lower (ut:hex-text $n)))
          ($pad (str:byte-slice "0000" 0 (- 4 (str:byte-length $digits)))))
     (str:concat $pad $digits)))
; One scalar as the regex escape \x{HEX}, and a range as LOW-HIGH.
(= (ut:escaped $c) (str:concat "\\x{" (str:concat (ut:hex-text $c) "}")))
(= (ut:escaped-range $lo $hi)
   (if (== $lo $hi) (ut:escaped $lo) (str:concat (ut:escaped $lo) (str:concat "-" (ut:escaped $hi)))))
; Text as a MeTTa string literal.
(= (ut:metta-string $text)
   (str:concat "\""
     (str:concat (str:join "\\\"" (str:split "\"" (str:join "\\\\" (str:split "\\" $text)))) "\"")))
(= (ut:member $x $xs)
   (if (== $xs ()) False (if (== (car-atom $xs) $x) True (ut:member $x (cdr-atom $xs)))))
(= (ut:last $xs) (if (== (cdr-atom $xs) ()) (car-atom $xs) (ut:last (cdr-atom $xs))))
(= (ut:lines $lines) (str:concat (str:join "\n" $lines) "\n"))

;; ── Small sets of scalars: sorted, disjoint, non-adjacent ranges ─────────

(= (ut:max $a $b) (if (> $a $b) $a $b))
(= (ut:domain) ((0 55295) (57344 1114111)))
; Prepend a range to a normalized list, merging what it overlaps or touches.
(= (ut:push $range $rest)
   (let ($lo $hi) $range
     (if (== $rest ()) (($lo $hi))
       (let ($rlo $rhi) (car-atom $rest)
         (if (<= $rlo (+ $hi 1))
             (ut:push ($lo (ut:max $hi $rhi)) (cdr-atom $rest))
             (cons-atom ($lo $hi) $rest))))))
(= (ut:union $a $b)
   (if (== $a ()) $b
     (if (== $b ()) $a
       (let* ((($alo $ahi) (car-atom $a)) (($blo $bhi) (car-atom $b)))
         (if (<= $alo $blo)
             (ut:push ($alo $ahi) (ut:union (cdr-atom $a) $b))
             (ut:push ($blo $bhi) (ut:union $a (cdr-atom $b))))))))
(= (ut:minus $a $b)
   (if (== $a ()) ()
     (if (== $b ()) $a
       (let* ((($xlo $xhi) (car-atom $a)) (($ylo $yhi) (car-atom $b)))
         (if (< $yhi $xlo) (ut:minus $a (cdr-atom $b))
           (if (> $ylo $xhi) (cons-atom ($xlo $xhi) (ut:minus (cdr-atom $a) $b))
             (let $after (if (> $xhi $yhi)
                             (ut:minus (cons-atom ((+ $yhi 1) $xhi) (cdr-atom $a)) (cdr-atom $b))
                             (ut:minus (cdr-atom $a) $b))
               (if (< $xlo $ylo) (cons-atom ($xlo (- $ylo 1)) $after) $after))))))))
(= (ut:intersect $a $b) (ut:minus $a (ut:minus $a $b)))
; Normalize ranges sorted by their low ends.
(= (ut:normalized $ranges)
   (if (== $ranges ()) () (ut:push (car-atom $ranges) (ut:normalized (cdr-atom $ranges)))))
(= (ut:points $cps) (ut:normalized (map-atom $cps $c ($c $c))))
(= (ut:class-body $ranges)
   (str:join "" (map-atom $ranges $range (let ($lo $hi) $range (ut:escaped-range $lo $hi)))))
; A regex class of exactly these scalars, or of all others.
(= (ut:class $ranges) (str:concat "[" (str:concat (ut:class-body $ranges) "]")))
(= (ut:negated-class $ranges) (str:concat "[^" (str:concat (ut:class-body $ranges) "]")))

;; ── The pinned Unicode Character Database ───────────────────────────────

; Arguments arrive as symbols; their names are the text.
(= (ut:arguments) (map-atom (system:args) $arg (str:symbol->string $arg)))
(= (ut:ucd-dir) (car-atom (ut:arguments)))
(= (ut:sources) (json:parse (fs:read-text (ut:at "config/unicode-sources-15.json"))))
(= (ut:verify-source $source)
   (let* (($relative (json:get $source "path"))
          ($path (path:join (ut:ucd-dir) $relative))
          ($_ (ut:require (fs:exists $path) (str:concat "missing Unicode data file " $relative)))
          ($_ (ut:require (== (fs:size $path) (json:get $source "bytes"))
                (str:concat "unqualified Unicode data file (size) " $relative)))
          ((fs:hashed-v1 $digest $info) (fs:sha256-file $path)))
     (ut:require (== $digest (json:get $source "sha256"))
       (str:concat "unqualified Unicode data file (SHA-256) " $relative))))
(= (ut:verify-sources)
   (let $sources (ut:sources)
     (let $_ (ut:require (== (json:get $sources "version") "15.0.0") "unqualified Unicode source list")
       (map-atom (json:array-items (json:get $sources "sources")) $source (ut:verify-source $source)))))

; The data lines of a UCD text: each line cut at its comment, blank lines
; dropped, fields trimmed.
(= (ut:data-lines $text)
   (filter-atom (map-atom (str:split "\n" $text) $line (str:trim (car-atom (str:split "#" $line))))
     $data (not (== $data ""))))
(= (ut:file-text $relative) (fs:read-text (path:join (ut:ucd-dir) $relative)))
(= (ut:file-lines $relative) (ut:data-lines (ut:file-text $relative)))
(= (ut:fields $line) (map-atom (str:split ";" $line) $field (str:trim $field)))
; A UCD code point field: XXXX or XXXX..YYYY, as (low high).
(= (ut:span $field)
   (let $ends (str:split ".." $field)
     (if (== (size-atom $ends) 1) ((ut:hex $field) (ut:hex $field))
       ((ut:hex (car-atom $ends)) (ut:hex (car-atom (cdr-atom $ends)))))))
; Space-separated code points, such as a mapping field.
(= (ut:code-points $field)
   (if (== $field "") () (map-atom (str:split " " $field) $hex (ut:hex $hex))))

; UnicodeData.txt as (UcdEntry low high category bidi decimal lowercase).
; A First/Last pair names a range; it is first written as one line whose
; code point field is the range.
(= (ut:paired $text)
   (re:replace-template
     (re:compile "(?m)^([0-9A-F]+);<([^,>]*), First>(;[^\n]*)\n([0-9A-F]+);<[^>]*, Last>;[^\n]*")
     $text "${1}..${4};<${2}>${3}"))
(= (ut:entry $line)
   (let* ((($cp $name $gc $ccc $bidi $dm $dec $dig $num $mir $old $iso $up $low $tit) (ut:fields $line))
          (($lo $hi) (ut:span $cp)))
     (UcdEntry $lo $hi $gc $bidi $dec $low)))
(= (ut:entries) (map-atom (ut:data-lines (ut:paired (ut:file-text "UnicodeData.txt"))) $line (ut:entry $line)))
(= (ut:has $kind $entry)
   (let (UcdEntry $lo $hi $gc $bidi $dec $low) $entry
     (case $kind
       ((letter (ut:member $gc ("Lu" "Ll" "Lt" "Lm" "Lo")))
        (graphic (not (ut:member (str:byte-slice $gc 0 1) ("C" "Z"))))
        (space (or (== $gc "Zs") (ut:member $bidi ("WS" "B" "S"))))
        (paragraph (== $bidi "B"))))))
(= (ut:selected $entries $kind) (filter-atom $entries $entry (ut:has $kind $entry)))
(= (ut:entry-span $entry) (let (UcdEntry $lo $hi $gc $bidi $dec $low) $entry ($lo $hi)))
; A class body of the selected entries, for a set only regex classes use;
; juxtaposed bodies are their union.
(= (ut:entry-body $entries $kind)
   (str:join "" (map-atom (ut:selected $entries $kind) $entry
     (let ($lo $hi) (ut:entry-span $entry) (ut:escaped-range $lo $hi)))))
; The selected entries as ranges, for a small set.
(= (ut:entry-ranges $entries $kind)
   (ut:normalized (map-atom (ut:selected $entries $kind) $entry (ut:entry-span $entry))))
; The lines of a property file whose value is one of $values.
(= (ut:value-lines $lines $values)
   (filter-atom $lines $line (ut:member (car-atom (cdr-atom (ut:fields $line))) $values)))
(= (ut:value-body $lines $values)
   (str:join "" (map-atom (ut:value-lines $lines $values) $line
     (let ($lo $hi) (ut:span (car-atom (ut:fields $line))) (ut:escaped-range $lo $hi)))))
; A small property, whose lines are in code point order, as ranges.
(= (ut:value-ranges $lines $values)
   (ut:normalized (map-atom (ut:value-lines $lines $values) $line (ut:span (car-atom (ut:fields $line))))))

;; ── Measurement against the engine's property classes ───────────────────

; The scalars of $ranges that $filter keeps, 4096 scalars per text: the
; filter deletes every run of scalars outside the class it measures.
(= (ut:kept $filter $ranges)
   (if (== $ranges ()) ()
     (let* ((($lo $hi) (car-atom $ranges))
            ($end (if (> $hi (+ $lo 4095)) (+ $lo 4095) $hi))
            ($here (str:python-codepoints
                     (re:replace-literal $filter (str:from-codepoints (list:range $lo (+ $end 1))) "")))
            ($more (if (< $end $hi) (cons-atom ((+ $end 1) $hi) (cdr-atom $ranges)) (cdr-atom $ranges))))
       (list:append $here (ut:kept $filter $more)))))
; The scalars in class $a and not in class $b, as ranges.
(= (ut:measured-difference $a $b)
   (ut:points (ut:kept (re:compile (str:concat "[^[" (str:concat $a (str:concat "--" (str:concat $b "]]+")))))
                       (ut:domain))))
(= (ut:corrected $expression $removed $added)
   (let $without
        (if (== $removed ()) $expression
          (str:concat "[" (str:concat $expression (str:concat "--" (str:concat (ut:class $removed) "]")))))
     (if (== $added ()) $without
       (str:concat "[" (str:concat $without (str:concat (ut:class $added) "]"))))))
; The set of class $explicit written as a correction over the engine's
; $expression, refused unless both compile to exactly the same pattern.
(= (ut:profile $expression $explicit)
   (let* (($removed (ut:measured-difference $expression $explicit))
          ($added (ut:measured-difference $explicit $expression))
          ($pattern (ut:corrected $expression $removed $added))
          ($_ (ut:require (== (re:store (re:compile $pattern)) (re:store (re:compile $explicit)))
                (str:concat "the correction over " (str:concat $expression " is not its explicit class")))))
     (UnicodeProfile $pattern $removed $added)))

;; ── The engine ──────────────────────────────────────────────────────────

; The revision of the running bundle, refused unless its manifest records
; this executable's digest and the revision CETTA_REF selects.
(= (ut:engine-revision)
   (let* (($exe (system:executable))
          ($_ (ut:require (== (get-type $exe) String) "the running engine cannot name its executable"))
          ($manifest-path (path:join (path:dirname $exe) "build-manifest.json"))
          ($_ (ut:require (fs:exists $manifest-path) "the running engine is not a bundle with a manifest"))
          ($manifest (json:parse (fs:read-text $manifest-path)))
          ($revision (json:get $manifest "revision"))
          ((fs:hashed-v1 $digest $info) (fs:sha256-file $exe))
          ($_ (ut:require (== $digest (json:get (json:get $manifest "files") (ut:last (path:parts $exe))))
                "the running engine does not match its manifest"))
          ($_ (ut:require (== $revision (str:trim (fs:read-text (ut:at "CETTA_REF"))))
                "the running engine is not the bundle CETTA_REF selects")))
     $revision))

;; ── Tables ──────────────────────────────────────────────────────────────

(= (ut:pattern-line $head $pattern)
   (str:concat "(= " (str:concat $head (str:concat " " (str:concat (ut:metta-string $pattern) ")")))))
(= (ut:header $revision $basis $what)
   ("; Generated by scripts/generate_unicode_tables.metta; do not edit."
    (str:concat "; Python 3.12 Unicode 15.0.0 " (str:concat $what (str:concat " CeTTa Unicode " (str:concat $basis "."))))
    (str:concat "; Scalar-domain corrections; selected CeTTa source " (str:concat $revision "."))))

; The affect trace an agent ends a reply with: ⋄⟨, anything but ⟩, then ⟩.
(= (ut:affect-open) "⋄⟨")
(= (ut:affect-close) "⟩")
(= (ut:affect-trace)
   (let $close (car-atom (str:python-codepoints (ut:affect-close)))
     (str:concat (str:join "" (map-atom (str:python-codepoints (ut:affect-open)) $c (ut:escaped $c)))
       (str:concat (ut:negated-class (($close $close))) (str:concat "*" (ut:escaped $close))))))

; Undelivered prose: line breaks, blank runs, spaces, words, alphanumerics
; and the affect trace.
(= (ut:prose-text $revision $basis $space $breaks $alnum)
   (ut:lines (list:append (ut:header $revision $basis "predicates over")
     ("(= (prose:unicode-version) \"15.0.0\")"
      (ut:pattern-line "(prose:pattern line-break)"
        (str:concat (ut:escaped 13) (str:concat (ut:escaped 10) (str:concat "|" (ut:class $breaks)))))
      (ut:pattern-line "(prose:pattern blank-run)" (str:concat (ut:class (ut:minus $space $breaks)) "+"))
      (ut:pattern-line "(prose:pattern space)" (ut:class $space))
      (ut:pattern-line "(prose:pattern word)" (str:concat (ut:negated-class $space) "+"))
      (ut:pattern-line "(prose:pattern alnum)" $alnum)
      (ut:pattern-line "(prose:pattern affect-trace)" (ut:affect-trace))))))

; Small views: what Python's repr escapes as not printable.
(= (ut:small-views-text $revision $basis $nonprintable)
   (ut:lines (list:append (ut:header $revision $basis "over")
     ((ut:pattern-line "(sv:nonprintable-pattern)" $nonprintable)))))

; The action window: decimal digits beyond ASCII by value, ascending, and
; the letters and numbers the engine's classes have that Unicode 15.0.0
; does not.
(= (ut:digit-row $digits $value)
   (let $class (str:concat "["
                 (str:concat (str:join "" (map-atom (filter-atom $digits $entry
                                                      (let (UcdEntry $lo $hi $gc $bidi $dec $low) $entry (== $dec $value)))
                                                    $entry (let ($lo $hi) (ut:entry-span $entry) (ut:escaped $lo))))
                   "]"))
     (str:concat "    (" (str:concat (ut:metta-string $class) (str:concat " \"" (str:concat $value "\")"))))))
(= (ut:action-window-text $entries $future)
   (let $digits (filter-atom $entries $entry
                  (let (UcdEntry $lo $hi $gc $bidi $dec $low) $entry (and (>= $lo 128) (not (== $dec "")))))
     (ut:lines (list:append
       ("; Generated by scripts/generate_unicode_tables.metta; do not edit."
        "; Python 3.12, Unicode 15.0.0 decimal digits."
        (ut:pattern-line "(aw:future-alnum)" (ut:class $future))
        "(= (aw:decimal-patterns)"
        "   (")
       (list:append (map-atom ("0" "1" "2" "3" "4" "5" "6" "7" "8" "9") $value (ut:digit-row $digits $value))
         ("   ))"))))))

;; ── Casefold ────────────────────────────────────────────────────────────

; Full case folding (status C and F) and full lowercase (SpecialCasing's
; unconditional mappings over UnicodeData's simple ones), as facts; each
; returns the code points it recorded.
(= (ut:record-folds $lines)
   (map-atom (filter-atom $lines $line (ut:member (car-atom (cdr-atom (ut:fields $line))) ("C" "F"))) $line
     (let* ((($code $status $mapping $tail) (ut:fields $line))
            ($cp (ut:hex $code)) ($m (ut:code-points $mapping))
            ($_ (add-atom &self (UcdFold $cp $m))))
       $cp)))
(= (ut:record-special-lowers $lines)
   (map-atom (filter-atom $lines $line (== (size-atom (ut:fields $line)) 5)) $line
     (let* ((($code $lower $title $upper $tail) (ut:fields $line))
            ($cp (ut:hex $code)) ($m (ut:code-points $lower))
            ($_ (add-atom &self (UcdSpecialLower $cp $m))))
       $cp)))
(= (ut:record-simple-lowers $entries)
   (map-atom (filter-atom $entries $entry (let (UcdEntry $lo $hi $gc $bidi $dec $low) $entry (not (== $low "")))) $entry
     (let* (((UcdEntry $lo $hi $gc $bidi $dec $low) $entry)
            ($m ((ut:hex $low)))
            ($_ (add-atom &self (UcdSimpleLower $lo $m))))
       $lo)))
(= (ut:found $results $default) (if (== $results ()) $default (car-atom $results)))
(= (ut:fold-of $c) (ut:found (collapse (match &self (UcdFold $c $m) $m)) ($c)))
(= (ut:lower-of $c)
   (ut:found (collapse (match &self (UcdSpecialLower $c $m) $m))
     (ut:found (collapse (match &self (UcdSimpleLower $c $m) $m)) ($c))))
; A text as Python's json.dumps writes it with ensure_ascii.
(= (ut:json-unit $u) (str:concat "\\u" (ut:lower-hex4 $u)))
(= (ut:json-char $c)
   (case $c
     ((34 "\\\"") (92 "\\\\") (8 "\\b") (9 "\\t") (10 "\\n") (12 "\\f") (13 "\\r")
      ($other
        (if (and (>= $other 32) (<= $other 126)) (str:from-codepoints ($other))
          (if (<= $other 65535) (ut:json-unit $other)
            (let (IntegerDivmodV1 $high $lowpart) (math:integer-divmod (- $other 65536) 1024)
              (str:concat (ut:json-unit (+ 55296 $high)) (ut:json-unit (+ 56320 $lowpart))))))))))
(= (ut:json-ascii $cps) (str:concat "\"" (str:concat (str:join "" (map-atom $cps $c (ut:json-char $c))) "\"")))
(= (ut:expand $ranges)
   (if (== $ranges ()) ()
     (let ($lo $hi) (car-atom $ranges) (list:append (list:range $lo (+ $hi 1)) (ut:expand (cdr-atom $ranges))))))
(= (ut:casefold-text $entries $fold-lines $special-lines)
   (let* (($folded (ut:points (ut:record-folds $fold-lines)))
          ($special (ut:points (ut:record-special-lowers $special-lines)))
          ($simple (ut:points (ut:record-simple-lowers $entries)))
          ($candidates (ut:expand (ut:union $folded (ut:union $special $simple))))
          ($differing (filter-atom $candidates $c (not (== (ut:fold-of $c) (ut:lower-of $c)))))
          ($rows (map-atom $differing $c
                   (str:concat "(UnicodeFoldDeltaV1 " (str:concat (repr $c) (str:concat " " (str:concat (ut:json-ascii (ut:fold-of $c)) ")")))))))
     (ut:lines (cons-atom "; Generated Unicode 15.0.0 full-casefold differences from scalar lowercase." $rows))))

;; ── Generation ──────────────────────────────────────────────────────────

(= (ut:tables)
   (let* (($revision (ut:engine-revision))
          ($_ (ut:verify-sources))
          ($basis (re:unicode-version))
          ($entries (ut:entries))
          ($numeric-lines (ut:file-lines "extracted/DerivedNumericType.txt"))
          ; str.isalnum: a letter, or Numeric_Type Decimal, Digit or Numeric,
          ; Unihan's numeric ideographs among them.
          ($alnum (str:concat "[" (str:concat (ut:entry-body $entries letter)
                    (str:concat (ut:value-body $numeric-lines ("Decimal" "Digit" "Numeric")) "]"))))
          ; str.isprintable: the space, or an assigned scalar outside C and Z.
          ($nonprintable (str:concat "[" (str:concat (ut:class (ut:domain))
                           (str:concat "--[\\x{20}" (str:concat (ut:entry-body $entries graphic) "]]")))))
          ; str.isspace: bidirectional WS, B or S, or category Zs; the line
          ; breaks among them are mandatory breaks and paragraph separators.
          ($space (ut:entry-ranges $entries space))
          ($mandatory (ut:value-ranges (ut:file-lines "LineBreak.txt") ("BK" "CR" "LF" "NL")))
          ($breaks (ut:intersect $space (ut:union $mandatory (ut:entry-ranges $entries paragraph))))
          ((UnicodeProfile $alnum-pattern $alnum-removed $alnum-added) (ut:profile "[\\p{L}\\p{N}]" $alnum))
          ((UnicodeProfile $np-pattern $np-removed $np-added) (ut:profile "[\\p{C}\\p{Z}]" $nonprintable))
          ($_ (ut:require (== $alnum-added ()) "the engine's property classes are older than Unicode 15.0.0")))
     (("src/undelivered_prose_unicode.metta" (ut:prose-text $revision $basis $space $breaks $alnum-pattern))
      ("src/small_views_unicode.metta" (ut:small-views-text $revision $basis $np-pattern))
      ("src/action_window_unicode.metta" (ut:action-window-text $entries $alnum-removed))
      ("config/unicode-casefold-15.metta"
        (ut:casefold-text $entries (ut:file-lines "CaseFolding.txt") (ut:file-lines "SpecialCasing.txt"))))))

(= (ut:stale $tables)
   (map-atom (filter-atom $tables $table
               (let* ((($relative $text) $table) ($path (ut:at $relative)))
                 (not (and (fs:exists $path) (== (fs:read-text $path) $text)))))
     $table (car-atom $table)))
(= (ut:main $args)
   (let* (($_ (ut:require (and (>= (size-atom $args) 1) (<= (size-atom $args) 2))
                "usage: generate_unicode_tables.metta UCD_DIR [--check]"))
          ($check (== (size-atom $args) 2))
          ($_ (ut:require (or (not $check) (== (car-atom (cdr-atom $args)) "--check"))
                "usage: generate_unicode_tables.metta UCD_DIR [--check]"))
          ($tables (ut:tables)))
     (if $check
         (let $stale (ut:stale $tables)
           (if (== $stale ()) 0
             (let $_ (map-atom $stale $relative
                       (system:write stderr (str:concat $relative " is stale; regenerate with the qualified CeTTa bundle\n")))
               1)))
         (let $_ (map-atom $tables $table (let ($relative $text) $table (fs:write (ut:at $relative) $text)))
           0))))

!(system:exit (ut:main (ut:arguments)))
