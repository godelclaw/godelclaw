; Native body of pins.context_view(): the bounded, newest-first PINNED block
; that the context bundle shows every turn, WITHOUT its change marker.
; pins.py stays the behavioural reference and the SWI-PeTTa backend. This file
; only reads: pin storage, pin/unpin and the full (pins) view are untouched.
;
; What the oracle's file read means, reproduced here:
;  - the file is UTF-8, strictly. Malformed bytes make the oracle raise, which
;    the bundle records as an unavailable (or stale-known) source, so here
;    they are an (Error ...) and the bundle's own loader answers instead;
;  - a missing, unreadable or directory path is an OSError there: no pins;
;  - the other errno failures the engine names (OSError and its subclasses:
;    not a directory, too many symlinks ...) are the same no pins; any
;    other native read error is left to the oracle (Error);
;  - text mode ends a line at \n, \r\n or a lone \r and at nothing else
;    (not \x0b, \x1c, \x85, \u2028 ...); a line is kept when it holds a
;    character that Python's str.strip() does not strip, and kept whole;
;  - the newest 12 lines show, each cut to 399 characters and "…" past 400.
;
; The change marker is NOT computed here. There is one owner of that state:
; helper.change_mark("pins-context", body), applied once by pins.mark_context_body
; at context_sources' pins loader, to this body or to the body pins.py's own
; reader made when this view is unavailable. Native therefore supplies the
; marker-free body, or "(no pins)" (which the oracle never marks).
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library system))
!(import! &self ./src/undelivered_prose_unicode)

(= (pinsctx:visible-count) 12)
(= (pinsctx:line-limit) 400)

!(bind! &pinsctx-newline (new-state (re:compile "\r\n?")))
; A blank line: only Python 3.12 whitespace other than \n (the generated
; space class, its \x{9}-\x{D} range split around \x{A}), then the line's end.
(= (pinsctx:blank-pattern)
   (str:join ""
     ("(?m)^"
      (str:join "\\x{9}\\x{B}-\\x{D}" (str:split "\\x{9}-\\x{D}" (prose:pattern space)))
      "*(?:\n|\\z)")))
!(bind! &pinsctx-blank (new-state (re:compile (pinsctx:blank-pattern))))

; os.path.join(os.path.dirname(history), "pins.txt"), spelled out because the
; path library's dirname differs ("sub/" is "sub" there, "." here, and a
; relative name has the empty directory, not "."). posixpath.dirname keeps
; everything up to the last "/", then drops trailing "/" unless all are.
!(bind! &pinsctx-slashes (new-state (re:compile "\\A/+\\z")))
!(bind! &pinsctx-trailing (new-state (re:compile "/+\\z")))
(= (pinsctx:last $parts)
   (if (== (cdr-atom $parts) ()) (car-atom $parts) (pinsctx:last (cdr-atom $parts))))
(= (pinsctx:dirname $path)
   (let* (($tail (pinsctx:last (str:split "/" $path)))
          ($head (str:byte-slice $path 0 (- (str:byte-length $path) (str:byte-length $tail)))))
     (if (or (== $head "") (re:is-match (get-state &pinsctx-slashes) $head)) $head
       (re:replace-literal (get-state &pinsctx-trailing) $head ""))))
(= (pinsctx:path)
   (system:getenv-or-default "METTACLAW_PINS_PATH"
     (pinsctx:beside (pinsctx:dirname
       (system:getenv-or-default "METTACLAW_HISTORY_PATH" "memory/history.metta")))))
(= (pinsctx:beside $directory)
   (if (or (== $directory "") (str:ends-with $directory "/"))
       (str:concat $directory "pins.txt")
       (str:join "/" ($directory "pins.txt"))))

(= (pinsctx:read)
   (case (fs:read-text-result (pinsctx:path) 0 "raw")
     ((("ok" $text)
       (case (str:codepoint-count $text)
         (((Error $call $why) (Error pinsctx:read invalid-utf8))
          ($count (PinsctxText $text)))))
      (("missing" $_) (PinsctxText ""))
      (("directory" $_) (PinsctxText ""))
      (("denied" $_) (PinsctxText ""))
      (("error" $class $_)
       (if (pinsctx:os-error? $class) (PinsctxText "") (Error pinsctx:read $class)))
      ($other (Error pinsctx:read $other)))))
; The classes the engine names for an errno: all OSError to the oracle.
(= (pinsctx:os-error? $class)
   (case $class
     (("OSError" True) ("FileNotFoundError" True) ("NotADirectoryError" True)
      ("IsADirectoryError" True) ("PermissionError" True) ($other False))))

; The kept lines, oldest first, and how many there are. A line ends at \n,
; \r\n or a lone \r: unify them, delete the blank lines in one native pass,
; and split what remains (a final \n leaves one empty piece, never a line).
(= (pinsctx:unify $text)
   (case (str:find $text "\r")
     (((TextNotFound $_) $text)
      ($at (re:replace-literal (get-state &pinsctx-newline) $text "\n")))))
(= (pinsctx:lines $text)
   (let* (($dense (re:replace-literal (get-state &pinsctx-blank) (pinsctx:unify $text) ""))
          ($pieces (str:split "\n" $dense))
          ($size (size-atom $pieces)))
     (PinsctxLines (if (== (index-atom $pieces (- $size 1)) "") (- $size 1) $size) $pieces)))

(= (pinsctx:clip $line)
   (if (<= (str:char-length $line) (pinsctx:line-limit)) $line
     (str:concat (str:char-slice $line 0 (- (pinsctx:line-limit) 1)) "…")))
; The newest $left of the lines below index $at, clipped, in file order.
(= (pinsctx:visible $pieces $at $left $out)
   (if (or (== $left 0) (== $at 0)) $out
     (let* (($i (- $at 1))
            ($next (cons-atom (pinsctx:clip (index-atom $pieces $i)) $out)))
       (pinsctx:visible $pieces $i (- $left 1) $next))))

(= (pinsctx:body $count $pieces)
   (let $visible (pinsctx:visible $pieces $count (pinsctx:visible-count) ())
     (str:join ""
       ("PINNED newest " (repr (size-atom $visible)) " of " (repr $count)
        "; (pins) reads the full retained list\n"
        (str:join " | " $visible)))))

; The marker-free view text, or (Error ...) when the file cannot be read as
; the oracle reads it.
(= (pinsctx:view)
   (case (pinsctx:read)
     (((Error $call $why) (Error pinsctx:view $why))
      ((PinsctxText $text)
       (case (pinsctx:lines $text)
         (((PinsctxLines 0 $_) "(no pins)")
          ((PinsctxLines $count $pieces) (pinsctx:body $count $pieces))))))))

; What the context bundle is handed: a (source-id text) pair, as for the
; action window, under "pins-body" (marker-free). A failed read is an
; (Error ...), never an empty text.
(= (pinsctx:context-views)
   (case (pinsctx:view)
     (((Error $call $why) (Error pinsctx:context-views $why))
      ($text (("pins-body" $text))))))
