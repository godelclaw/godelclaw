; Pins: every pin is retained, also when 24 engines pin at once; long pins
; sharing a prefix stay distinct; the context shows the newest pins within a
; bound and says how to read the full list, which (pins) gives.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/state_cetta.metta)
!(import! &self ../../src/pins_context_cetta.metta)
!(import! &self (library list))

(= (pt:path) (nt:required-env "METTACLAW_PINS_PATH"))
(= (pt:lines) (filter-atom (str:split "\n" (fs:read-text (pt:path))) $line (not (== $line ""))))
(= (pt:pinner $root $index)
   (let* (($source (path:join (nt:required-env "HOME") (str:join "" ("pin-" (repr $index) ".metta"))))
          ($_ (fs:write $source (str:join "" ("!(import! &self " (repr (path:join $root "src/state_cetta.metta")) ")\n"
                                              "!(println! (pins:cetta-pin \"pin-" (repr $index) "\"))\n")))))
     (proc:spawn ((path:join (nt:required-env "NATIVE_TEST_CORE") "cetta") "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet" $source)
                 ((cwd $root) (env (cons-atom ("METTACLAW_PINS_PATH" (pt:path)) (nt:environment (nt:required-env "HOME"))))
                  (max-bytes 65536) (lifetime owner)))))

!(let* (($root (nt:required-env "TEST_REPOSITORY"))
        ($pinners (map-atom (list:range 0 24) $i (pt:pinner $root $i)))
        ($ended (map-atom $pinners $p (proc:wait $p 120000)))
        ($lines (pt:lines)))
   (nt:require concurrent-pins-are-all-retained
     (and (== (size-atom $lines) 24)
     (and (== (size-atom (unique-atom (map-atom $lines $l (pins:body $l)))) 24)
          (not (== (pins:cetta-view) "(no pins)"))))))

!(let* (($_ (fs:write (pt:path) ""))
        ($prefix (str:join "" (map-atom (list:range 0 100) $i "same prefix ")))
        ($_ (pins:cetta-pin (str:concat $prefix "first")))
        ($_ (pins:cetta-pin (str:concat $prefix "second")))
        ($lines (pt:lines)))
   (nt:require long-pins-with-a-shared-prefix-stay-distinct
     (and (== (size-atom $lines) 2)
     (and (str:ends-with (car-atom $lines) (str:concat $prefix "first"))
     (and (str:ends-with (car-atom (cdr-atom $lines)) (str:concat $prefix "second"))
     (and (nt:contains (pins:cetta-view) "first") (nt:contains (pins:cetta-view) "second")))))))

!(let* (($long (str:join "" (map-atom (list:range 0 500) $i "x")))
        ($_ (fs:write (pt:path) (str:concat (str:join "\n" (map-atom (list:range 0 30) $i
              (str:join "" ("[" (if (< $i 10) "0" "") (repr $i) "] " $long (repr $i))))) "\n")))
        ($view (pinsctx:view)))
   (nt:require the-context-shows-the-newest-pins-and-names-the-rest
     (and (nt:contains $view "newest 12 of 30")
     (and (nt:contains $view "(pins) reads the full retained list")
     (and (nt:contains $view "[29]")
     (and (not (nt:contains $view "[00]"))
     (and (< (str:char-length $view) 6000)
          (> (str:char-length (pins:cetta-view)) (str:char-length $view)))))))))

!(println! UNIT_PINS_OK)
