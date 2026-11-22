!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/components_cetta.metta)
; The skill index reads only each file's first line, however long that line
; or the body after it.

; 2^n copies of a text.
(= (sfl:doubled $text $n) (if (== $n 0) $text (sfl:doubled (str:concat $text $text) (- $n 1))))

!(let* (($dir (path:join (nt:required-env "HOME") "skill-first-line"))
        ($_ (fs:make-directories $dir 448))
        ($long (str:concat "DESCRIPTION=\"" (str:concat (sfl:doubled "x" 13) "\"")))
        ($_ (fs:write (path:join $dir "long.md") (str:concat $long "\nbody\n")))
        ($_ (fs:write (path:join $dir "crlf.md") "DESCRIPTION=\"crlf\"\r\nbody\r\n"))
        ($_ (fs:write (path:join $dir "big.md") (str:concat "DESCRIPTION=\"big\"\n" (sfl:doubled "body line\n" 17))))
        ($_ (fs:write (path:join $dir "bare.md") "DESCRIPTION=\"no line end\""))
        ($_ (fs:write (path:join $dir "empty.md") ""))
        ($_ (fs:write (path:join $dir "heading.md") "# Title\nDESCRIPTION=\"second line\"\n"))
        ($_ (fs:write (path:join $dir "cr.md") "DESCRIPTION=\"cr only\"\rbody\r"))
        ; One byte 0xFF inside the declaration: replaced, as a whole-file read replaced it.
        ($ff (path:join $dir "invalid.md"))
        ($_ (nt:exit-ok (proc:run ("/bin/sh" "-c" "printf 'DESCRIPTION=\"invalid \\377 byte\"\\nrest\\n' > \"$1\"" "sh" $ff)
                              "/" inherit 10000 4096)))
        ($_ (nt:require long-first-line-whole (== (component:skill-description (path:join $dir "long.md")) $long)))
        ($_ (nt:require crlf-trimmed (== (component:skill-description (path:join $dir "crlf.md")) "DESCRIPTION=\"crlf\"")))
        ($_ (nt:require big-body-first-line (== (component:skill-description (path:join $dir "big.md")) "DESCRIPTION=\"big\"")))
        ($_ (nt:require no-line-end (== (component:skill-description (path:join $dir "bare.md")) "DESCRIPTION=\"no line end\"")))
        ($_ (nt:require empty-missing (== (component:skill-description (path:join $dir "empty.md")) "[DESCRIPTION FIRST LINE MISSING]")))
        ($_ (nt:require first-line-only (== (component:skill-description (path:join $dir "heading.md")) "[DESCRIPTION FIRST LINE MISSING]")))
        ($_ (nt:require invalid-utf8-replaced (== (component:skill-description $ff) "DESCRIPTION=\"invalid \uFFFD byte\"")))
        ($_ (nt:require cr-ends-the-line (== (component:skill-description (path:join $dir "cr.md")) "DESCRIPTION=\"cr only\"")))
        ($_ (nt:require unreadable-reported (str:starts-with (component:skill-description (path:join $dir "absent.md")) "[READ ERROR: "))))
   (println! UNIT_SKILL_FIRST_LINE_OK))
