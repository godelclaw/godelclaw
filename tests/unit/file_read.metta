; The whole-file read skill on the files of tests/fixtures/file_read_tree.sh:
; each file reads as its UTF-8 text, every malformed sequence one U+FFFD, as
; Python's decoder answered (tests/fixtures/file_read_reference.json); an
; empty file says so; the default budget refuses past 2,000,000 bytes and
; names the way out, a budget of 0 or below reads everything, and a budget
; given as a string or a float reads as its whole number; a missing file, a
; directory and an unreadable file each say what to do. A line window shows a
; line whole, however long.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/file_io.metta)
!(import! &self (library json))

(= (fr:work) (path:join (nt:required-env "HOME") "read"))
(= (fr:path $name) (path:join (fr:work) $name))
(= (fr:reference)
   (json:get (json:parse (fs:read-text (path:join (nt:required-env "TEST_REPOSITORY") "tests/fixtures/file_read_reference.json")))
             "decoded_sha256"))
(= (fr:digest-of $text) (str:sha256 $text))
(= (fr:reads-as-python? $name $text) (== (fr:digest-of $text) (json:get (fr:reference) $name)))
(= (fr:exceeds $path $limit)
   (str:join "" ("read failed: " $path " exceeds " $limit
                 " bytes — use read-lines for a window, or supply a larger max_bytes (0 means unlimited)")))

!(let* (($root (nt:required-env "TEST_REPOSITORY"))
        ($made (proc:run ("sh" (path:join $root "tests/fixtures/file_read_tree.sh") (fr:work)) $root
                         (nt:environment (nt:required-env "HOME")) 60000 4096)))
   (nt:require the-fixture-files-are-written (case $made (((proc:exited 0 $o $e) True) ($x False)))))

!(nt:require every-file-reads-as-its-utf8-text
   (== (filter-atom ("unicode" "long-line" "nul" "malformed" "all-byte-pairs" "random") $name
         (not (fr:reads-as-python? $name (read-file (fr:path $name)))))
       ()))
!(nt:require an-empty-file-says-it-is-empty
   (== (read-file (fr:path "empty"))
       (str:join "" ("READ_OK_EMPTY_FILE: " (fr:path "empty") " exists and holds zero bytes"))))
!(nt:require the-default-budget-refuses-and-names-the-way-out
   (== (read-file (fr:path "large")) (fr:exceeds (fr:path "large") "2000000")))
!(nt:require a-budget-of-zero-or-below-reads-everything
   (and (fr:reads-as-python? "large" (read-file (fr:path "large") 0))
        (fr:reads-as-python? "large" (read-file (fr:path "large") -1))))
!(nt:require a-budget-reads-as-its-whole-number
   (== (map-atom (3 "3" 3.9 0) $budget (read-file (fr:path "boundary") $budget)) ("abc" "abc" "abc" "abc")))
!(nt:require one-byte-short-is-refused
   (== (read-file (fr:path "boundary") 2) (fr:exceeds (fr:path "boundary") "2")))
!(nt:require a-missing-file-names-the-directory-it-was-looked-for-in
   (== (read-file (fr:path "missing"))
       (str:join "" ("read failed: no such file: " (fr:path "missing") " (cwd " (system:cwd) ")"))))
!(nt:require a-directory-points-to-ls-tree
   (== (read-file (fr:path "directory"))
       (str:join "" ("read failed: " (fr:path "directory") " is a directory — use (ls-tree " (repr (fr:path "directory")) " 1)"))))
!(nt:require an-unreadable-file-says-permission-denied
   (or (== (system:getenv-or-default "USER" "") "root")
       (== (read-file (fr:path "denied")) (str:concat "read failed: permission denied: " (fr:path "denied")))))
!(nt:require a-line-window-shows-a-long-line-whole
   (== (read-lines (fr:path "large") 1 1)
       (str:join "" ((fr:path "large") ": showing 1-1; end of file\n    1\t" (fs:read-text (fr:path "large"))))))

!(println! UNIT_FILE_READ_OK)
