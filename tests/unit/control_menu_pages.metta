; Large catalogues stay fully reachable through small menu pages: every
; choice of 500 long names is reached page by page, each page under 100
; rows and every callback within Telegram's 64 bytes; the Next button opens
; the next page. Model pages are grouped by provider, each keeping its exact
; choice; an old flat page returns to the providers; an unknown provider and
; an expired choice are refused rather than selecting something else.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/control_menu_cetta.metta)
!(import! &self (library json))
!(import! &self (library list))

; The catalogue a case shows, in place of the live model state.
!(bind! &cm-names (new-state ()))
!(match &self (= (menu:state $prefix) $body) (remove-atom &self (= (menu:state $prefix) $body)))
(= (menu:state $prefix)
   (let $names (get-state &cm-names) (MenuStateV1 $names (if (== $names ()) "" (car-atom $names)) "" () "models")))

; A page's rows, each a list of buttons, each (label data).
(= (cm:rows $page)
   (map-atom (json:array-items (json:get $page "rows")) $row
     (map-atom (json:array-items $row) $b (json:array-items $b))))
(= (cm:buttons $page) (foldl-atom (cm:rows $page) () $acc $row (list:append $acc $row)))
(= (cm:data $button) (index-atom $button 1))
(= (cm:after $text $prefix) (str:byte-slice $text (str:byte-length $prefix) (str:byte-length $text)))
; Walk the pages of one provider, collecting the names its choices select.
(= (cm:walk $offset $seen $ok)
   (let* (($page (menu:page "model" $offset "synthetic"))
          ($buttons (cm:buttons $page))
          ($fits (and (< (size-atom (cm:rows $page)) 100)
                      (== (filter-atom $buttons $b (> (str:byte-length (cm:data $b)) 64)) ())))
          ($names (foldl-atom $buttons () $acc $b
                    (let $data (cm:data $b)
                      (if (str:starts-with $data "choice:") (list:append $acc ((cm:after (menu:button-choice (cm:after $data "choice:")) "model:")))
                        (if (str:starts-with $data "model:") (list:append $acc ((cm:after $data "model:"))) $acc)))))
          ($next (str:join "" ("menu:model:synthetic:" (repr (+ $offset 24)))))
          ($has-next (not (== (filter-atom $buttons $b (== (cm:data $b) $next)) ())))
          ($same (if $has-next (== (menu:button-page (cm:after $next "menu:")) (menu:page "model" (+ $offset 24) "synthetic")) True)))
     (if $has-next (cm:walk (+ $offset 24) (list:append $seen $names) (and $ok (and $fits $same)))
       (CmWalkV1 (list:append $seen $names) (and $ok $fits)))))

!(let* (($long (str:join "" (map-atom (list:range 0 8) $i "long-model-name-")))
        ($names (map-atom (list:range 0 500) $n (str:join "" ("provider/" $long (repr $n)))))
        ($_ (change-state! &cm-names $names))
        ((CmWalkV1 $seen $ok) (cm:walk 0 () True)))
   (nt:require every-long-choice-is-reachable-in-pages-that-fit
     (and $ok (== $seen $names))))

!(let* (($names ("kimi:k3.1-pro" "hf:moonshotai/Kimi-K3.1-Pro" "claude-fixture" "chatgpt:fixture" "openai:fixture"))
        ($_ (change-state! &cm-names $names))
        ($root (menu:page "model" 0 ""))
        ($roots (cm:rows $root))
        ($per-provider (map-atom $roots $row
          (let* (($page (menu:button-page (cm:after (cm:data (car-atom $row)) "menu:")))
                 ($choices (filter-atom (cm:buttons $page) $b (str:starts-with (cm:data $b) "model:")))
                 ($last (car-atom (list:drop (- (size-atom (cm:rows $page)) 1) (cm:rows $page)))))
            (if (and (== (size-atom $choices) 1) (== (map-atom $last $b (cm:data $b)) ("menu:model:providers")))
              (cm:after (cm:data (car-atom $choices)) "model:") Misplaced))))
        ($_ (nt:require provider-pages-keep-each-exact-choice
              (and (== (size-atom $roots) 5)
              (and (nt:contains (index-atom (car-atom (car-atom $roots)) 0) "Kimi Code")
                   (== (sort-atom $per-provider) (sort-atom $names))))))
        ($_ (nt:require the-providers-button-and-an-old-flat-page-return-to-the-providers
              (and (== (menu:button-page "model:providers") $root) (== (menu:button-page "model:24") $root)))))
   (nt:require an-unknown-provider-is-refused
     (case (collapse (menu:button-page "model:unrecognized:0"))
       ((((Error $where $why)) (nt:contains (repr $why) "unknown-model-provider")) ($other False)))))

!(let $_ (change-state! &cm-names ())
   (nt:require an-expired-choice-is-refused
     (case (collapse (menu:button-choice (str:concat "model:" (str:join "" (map-atom (list:range 0 40) $i "a")))))
       ((((Error $where $why)) True) ($other False)))))

!(println! UNIT_CONTROL_MENU_PAGES_OK)
