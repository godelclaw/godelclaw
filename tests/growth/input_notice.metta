; Growth workload: the responder's notice bookkeeping over one page of n
; pending operator messages on eight lanes, without a ledger: the scan with
; its inbox view, a key decision per message before and after the delay, a
; submission per lane, and the scan that forgets them all. A stopped agent
; can leave many messages pending, so each step must stay linear in them.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/input_notice_cetta.metta)
(= (ng:lane $n) (str:concat "lane." (repr (% $n 8))))
(= (ng:page $n $out)
   (if (== $n 0) $out
     (ng:page (- $n 1)
       (cons-atom ((str:concat "task-" (repr $n))
                   (str:join "" ("[\"input\",\"" (ng:lane $n) "\",\"message\",\"operator\",{\"update_id\":" (repr $n)
                                 ",\"message\":{\"date\":" (repr (+ 1700000000 $n)) ",\"text\":\"hello there\"}}]")))
                  $out))))
(= (ng:keys $config $now $n $sent)
   (if (== $n 0) $sent
     (let $key (notice:key-at $config $now (str:concat "task-" (repr $n)) (ng:lane $n) "message" "operator" (JsonObject ()))
       (if (== $key "") (ng:keys $config $now (- $n 1) $sent)
         (let $_ (notice:submitted-at $config $now (str:concat "task-" (repr $n)))
           (ng:keys $config $now (- $n 1) (+ $sent 1)))))))
(= (ng:run $n)
   (let* (($config (NoticeConfigV1 "" True "growth" 15)) ($page (ng:page $n ()))
          ($_ (notice:begin-scan-at 1.0 $page $page))
          ((JsonObject $view) (notice:input-view))
          ($first (ng:keys $config 100.0 $n 0))
          ($sent (ng:keys $config 115.0 $n 0))
          ($held (notice:pending-count))
          ($_ (notice:begin-scan-at 2.0 () ())))
     (+ (+ $first $sent) (+ $held (notice:pending-count)))))
!(growth:measure (ng:run (growth:size)))
