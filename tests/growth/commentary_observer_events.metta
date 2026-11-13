; Growth workload: a streamed call's commentary observer takes n SSE events
; that are no source (reasoning deltas), sixteen to a read. Each event must
; cost the same however many came before.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/worklog_sink_cetta.metta)
!(import! &self ../../src/commentary_observer_cetta.metta)
(= (og:frame) "data: {\"id\":\"r\",\"choices\":[{\"index\":0,\"delta\":{\"reasoning_content\":\"thinking it over\"},\"finish_reason\":null}]}\n\n")
(= (og:read $copies) (if (== $copies 0) "" (str:concat (og:frame) (og:read (- $copies 1)))))
; Fails if the observer stopped: a stopped observer measures nothing.
(= (og:feed $text $left)
   (if (<= $left 0) (nt:require observer-still-reading (not (== (observer:offset "growth") none)))
     (let $_ (observer:feed "growth" (+ (observer:offset "growth") (str:byte-length $text)) $text waiting)
       (og:feed $text (- $left 16)))))
!(let* (($_ (observer:open "growth" 1 "chat-completions" True 1073741824)) ($text (og:read 16)))
   (growth:measure (og:feed $text (growth:size))))
