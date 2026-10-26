; Python 3.12's 29 whitespace code points, with surrogate-preserving strings.
; ASCII lowering is sufficient for membership in the finite ASCII mode names:
; no non-ASCII character lowercases into a letter in those names. This is NOT
; a general replacement for Python str.lower on text returned to the caller.
!(import! &self (library str))
!(import! &self (library list))

(= (mode-text:space? $cp)
   (or (and (>= $cp 9) (<= $cp 13))
     (or (and (>= $cp 28) (<= $cp 32))
       (or (== $cp 133) (or (== $cp 160) (or (== $cp 5760)
         (or (and (>= $cp 8192) (<= $cp 8202))
           (or (== $cp 8232) (or (== $cp 8233)
             (or (== $cp 8239) (or (== $cp 8287) (== $cp 12288))))))))))))
(= (mode-text:points $text) (list:to-expression (str:python-codepoints $text)))
(= (mode-text:lstrip $points)
   (if (== $points ()) ()
     (if (mode-text:space? (car-atom $points))
       (mode-text:lstrip (cdr-atom $points)) $points)))
(= (mode-text:strip $text)
   (let $points (mode-text:points $text)
     (if (== $points ()) ""
       (if (and (not (mode-text:space? (car-atom $points)))
                (not (mode-text:space? (index-atom $points (- (size-atom $points) 1))))) $text
         (str:from-python-codepoints (list:reverse (mode-text:lstrip
           (list:reverse (mode-text:lstrip $points)))))))))
(= (mode-text:name $text) (str:lower (mode-text:strip $text)))
(= (mode-text:universal $text)
   (str:join "\n" (str:split "\r" (str:join "\n" (str:split "\r\n" $text)))))
; Environment/path bytes use CPython's POSIX UTF-8 surrogateescape, NOT the
; strict file-content decoder or surrogatepass JSON decoder. Use this only
; when presenting an OS path as Python text; filesystem operations retain the
; original bytes. Each undecodable byte >=128 maps to U+DC00 + byte.
(= (mode-text:os-text $bytes)
   (case (str:utf8-error $bytes)
     ((StrUtf8WellFormedV1 $bytes)
      ((StrUtf8ErrorV1 $start $_ $byte $_)
       (str:join "" ((str:byte-slice $bytes 0 $start)
         (str:from-python-codepoints ((+ 56320 $byte)))
         (mode-text:os-text (str:byte-slice $bytes (+ $start 1) (str:byte-length $bytes))))))
      ($other (Error mode-text:os-text $other)))))
