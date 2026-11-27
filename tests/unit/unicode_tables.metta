; The checked-in Unicode tables, applied by the running engine, select what
; Python 3.12 selects under Unicode 15.0.0. Each corrected profile's members
; over every scalar have the count and digest that two derivations agreed
; on: Python 3.12's unicodedata, which wrote the tables, and
; scripts/generate_unicode_tables.metta reading the pinned UCD files. An
; engine whose property classes change fails here; regenerate the tables
; with it. The engine's plain classes are not enough (U+1C89, a letter new
; in Unicode 16, and the space), and the generator refuses, writing
; nothing, an engine that is not CETTA_REF's bundle, data that is not the
; pinned files, and arguments it does not take.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/undelivered_prose_unicode.metta)
!(import! &self ../../src/small_views_unicode.metta)
!(import! &self ../../src/action_window_unicode.metta)
!(import! &self (library re))
!(import! &self (library list))

(= (ux:root) (nt:required-env "TEST_REPOSITORY"))
(= (ux:engine) (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))
(= (ux:home) (nt:required-env "HOME"))

; The scalars of $ranges that $filter keeps, 4096 per text.
(= (ux:kept $filter $ranges)
   (if (== $ranges ()) ()
     (let* ((($lo $hi) (car-atom $ranges))
            ($end (if (> $hi (+ $lo 4095)) (+ $lo 4095) $hi))
            ($here (str:python-codepoints
                     (re:replace-literal $filter (str:from-codepoints (list:range $lo (+ $end 1))) "")))
            ($more (if (< $end $hi) (cons-atom ((+ $end 1) $hi) (cdr-atom $ranges)) (cdr-atom $ranges))))
       (list:append $here (ux:kept $filter $more)))))
; The members of a class among all scalars: their count and the SHA-256 of
; their UTF-8 text in ascending order.
(= (ux:members $class)
   (let $cps (ux:kept (re:compile (str:concat "[^" (str:concat $class "]+"))) ((0 55295) (57344 1114111)))
     ((size-atom $cps) (str:sha256 (str:from-codepoints $cps)))))
(= (ux:matches $class $text) (re:is-match (re:compile $class) $text))

; The generator, copied with its source list into a scratch repository whose
; CETTA_REF is $revision, beside tables that must survive a refusal.
(= (ux:scratch-repository $name $revision)
   (let* (($repo (path:join (ux:home) $name))
          ($_ (fs:make-directories (path:join $repo "scripts") 448))
          ($_ (fs:make-directories (path:join $repo "config") 448))
          ($_ (fs:make-directories (path:join $repo "src") 448))
          ($_ (fs:write (path:join $repo "scripts/generate_unicode_tables.metta")
                (fs:read-text (path:join (ux:root) "scripts/generate_unicode_tables.metta"))))
          ($_ (fs:write (path:join $repo "config/unicode-sources-15.json")
                (fs:read-text (path:join (ux:root) "config/unicode-sources-15.json"))))
          ($_ (fs:write (path:join $repo "CETTA_REF") (str:concat $revision "\n")))
          ($_ (fs:write (path:join $repo "src/undelivered_prose_unicode.metta") "kept\n")))
     $repo))
(= (ux:generate $repo $arguments)
   (proc:run (list:append ((ux:engine) "--lang" "petta" "--profile" "extended" "--quiet"
                           (path:join $repo "scripts/generate_unicode_tables.metta"))
                          $arguments)
             $repo (nt:environment (ux:home)) 60000 1048576))
; Refused with $reason, exit 2, and the scratch tables untouched.
(= (ux:refused $repo $run $reason)
   (case $run
     (((proc:exited 2 $out $err)
       (and (nt:contains $err (str:concat "REFUSE: " $reason))
            (== (fs:read-text (path:join $repo "src/undelivered_prose_unicode.metta")) "kept\n")))
      ($other (let $_ (println! (UNEXPECTED_GENERATION $reason $other)) False)))))

!(let* (($revision (str:trim (fs:read-text (path:join (ux:root) "CETTA_REF"))))
        ($_ (nt:require corrected-alnum-is-python-3-12-isalnum
              (nt:equal (ux:members (prose:pattern alnum))
                        (137935 "1913bb6554c1c163c7425aa0034d832aa0e071ce071394143945cd762a13ecb0"))))
        ($_ (nt:require corrected-nonprintable-is-python-3-12-not-isprintable
              (nt:equal (ux:members (sv:nonprintable-pattern))
                        (963066 "d7d3546c584238755a82b5ba0da1761abd35561f36ab3eafbec28a9b88c6d390"))))
        ($_ (nt:require future-alnum-is-what-the-engine-adds
              (nt:equal (ux:members (aw:future-alnum))
                        (5004 "d41fcfbc15cf4a5125c9bed4b8e2938e47df30f4019696a72f55484405ae1116"))))
        ($_ (nt:require the-plain-classes-need-the-corrections
              (and (and (ux:matches "[\\p{L}\\p{N}]" "Ᲊ") (not (ux:matches (prose:pattern alnum) "Ᲊ")))
                   (and (and (not (ux:matches "[\\p{C}\\p{Z}]" "Ᲊ")) (ux:matches (sv:nonprintable-pattern) "Ᲊ"))
                        (and (ux:matches "[\\p{C}\\p{Z}]" " ") (not (ux:matches (sv:nonprintable-pattern) " ")))))))
        ($_ (nt:require the-small-predicates-are-python-3-12-s
              (and (== (prose:pattern line-break) "\\x{D}\\x{A}|[\\x{A}-\\x{D}\\x{1C}-\\x{1E}\\x{85}\\x{2028}-\\x{2029}]")
              (and (== (prose:pattern blank-run) "[\\x{9}\\x{1F}-\\x{20}\\x{A0}\\x{1680}\\x{2000}-\\x{200A}\\x{202F}\\x{205F}\\x{3000}]+")
              (and (== (prose:pattern space) "[\\x{9}-\\x{D}\\x{1C}-\\x{20}\\x{85}\\x{A0}\\x{1680}\\x{2000}-\\x{200A}\\x{2028}-\\x{2029}\\x{202F}\\x{205F}\\x{3000}]")
              (and (== (prose:pattern word) "[^\\x{9}-\\x{D}\\x{1C}-\\x{20}\\x{85}\\x{A0}\\x{1680}\\x{2000}-\\x{200A}\\x{2028}-\\x{2029}\\x{202F}\\x{205F}\\x{3000}]+")
                   (== (prose:pattern affect-trace) "\\x{22C4}\\x{27E8}[^\\x{27E9}]*\\x{27E9}")))))))
        ($other-engine (ux:scratch-repository "other-engine" "0000000000000000000000000000000000000000"))
        ($_ (nt:require generator-refuses-an-engine-cetta-ref-does-not-select
              (ux:refused $other-engine (ux:generate $other-engine ((path:join (ux:home) "ucd")))
                "the running engine is not the bundle CETTA_REF selects")))
        ($repo (ux:scratch-repository "repo" $revision))
        ($ucd (path:join (ux:home) "ucd"))
        ($_ (fs:make-directories (path:join $ucd "extracted") 448))
        ($_ (nt:require generator-refuses-missing-data
              (ux:refused $repo (ux:generate $repo ($ucd)) "missing Unicode data file UnicodeData.txt")))
        ($_ (map-atom ("UnicodeData.txt" "LineBreak.txt" "CaseFolding.txt" "SpecialCasing.txt"
                       "extracted/DerivedNumericType.txt")
              $relative (fs:write (path:join $ucd $relative) "0041;LATIN CAPITAL LETTER A;Lu;0;L;;;;;N;;;;0061;\n")))
        ($_ (nt:require generator-refuses-data-that-is-not-the-pinned-files
              (ux:refused $repo (ux:generate $repo ($ucd)) "unqualified Unicode data file (size) UnicodeData.txt")))
        ($_ (nt:require generator-refuses-arguments-it-does-not-take
              (and (ux:refused $repo (ux:generate $repo ()) "usage:")
                   (ux:refused $repo (ux:generate $repo ($ucd "--chek")) "usage:")))))
   (println! UNIT_UNICODE_TABLES_OK))
