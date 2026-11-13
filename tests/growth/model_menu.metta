; Growth workload: the model menu's state with n models in the observed
; catalog (and n/4 in Kimi Code's), a quarter of them listed twice. Both
; catalogs are fresh, so no refresh child starts.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/model_menu_cetta.metta)
(= (menu-growth:rows $n $prefix $acc)
   (if (== $n 0) $acc
     (menu-growth:rows (- $n 1) $prefix
       (cons-atom (str:join "" ("{\"id\": \"" $prefix (repr (% $n (+ 1 (/ (* 3 (growth:size)) 4)))) "\", \"context\": 128000}")) $acc))))
(= (menu-growth:record $kind $rows $now)
   (str:join "" ("\"" $kind "\": {\"observed_at\": " $now ", \"attempted_at\": " $now ", \"value\": [" (str:join ", " $rows) "]}")))
!(let* (($now (json:stringify-python (system:wall-time)))
        ($path (model:observation-path))
        ($_ (fs:make-directories (path:dirname $path) 448))
        ($_ (fs:write $path (str:join "" ("{\"schema\": 1, "
               (menu-growth:record "models" (menu-growth:rows (growth:size) "model-" ()) $now) ", "
               (menu-growth:record "kimi-models" (menu-growth:rows (/ (growth:size) 4) "kimi:k-" ()) $now) "}")))))
   (growth:measure (model:menu-state)))
