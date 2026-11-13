; Growth workload: a streamed commentary field of n characters, eight to an
; event and four events to a read, that has not ended. The observer keeps it
; to its 16 KiB bound: each character must cost the same however many came
; before.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/worklog_sink_cetta.metta)
!(import! &self ../../src/commentary_observer_cetta.metta)
(= (og:frame $content)
   (str:concat "data: {\"id\":\"r\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\""
     (str:concat $content "\"},\"finish_reason\":null}]}\n\n")))
; Fails if the observer stopped: a stopped observer measures nothing.
(= (og:feed $text $left)
   (if (<= $left 0) (nt:require observer-still-reading (not (== (observer:offset "growth") none)))
     (let $_ (observer:feed "growth" (+ (observer:offset "growth") (str:byte-length $text)) $text waiting)
       (og:feed $text (- $left 32)))))
!(let* (($_ (observer:open "growth" 1 "chat-completions" True 1073741824))
        ($read (str:concat (og:frame "abcdefgh") (str:concat (og:frame "abcdefgh") (str:concat (og:frame "abcdefgh") (og:frame "abcdefgh")))))
        ($_ (observer:feed "growth" (str:byte-length (og:frame "{\\\"commentary\\\": \\\"")) (og:frame "{\\\"commentary\\\": \\\"") waiting)))
   (growth:measure (og:feed $read (growth:size))))
