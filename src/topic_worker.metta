; A separate CeTTa process owns one transport handle and cannot dispatch tools.
!(import! &self ./topic_worker.py)
!(import! &self io)

(: topic-worker:after-submit (-> Atom Atom %Undefined%))
(= (topic-worker:after-submit $plan $outcome)
   (case $outcome
     (((io:pending $id)
       (progn (py-call (topic_worker.submitted $plan)) (topic-worker:wait $plan $id)))
      ($other (py-call (topic_worker.failed $plan "submit-error"))))))

(: topic-worker:wait (-> Atom Atom %Undefined%))
(= (topic-worker:wait $plan $id)
   (let $event (io:wait 250) (topic-worker:observe $plan $id $event)))

(: topic-worker:observe (-> Atom Atom Atom %Undefined%))
(= (topic-worker:observe $plan $id $event)
   (case $event
     (((io:idle) (topic-worker:wait $plan $id))
      ((io:event $event-id (http:response $status $body))
       (if (== $id $event-id) (py-call (topic_worker.finish $plan $status $body))
           (py-call (topic_worker.failed $plan "foreign-event"))))
      ($other (py-call (topic_worker.failed $plan "transport-error"))))))

!(let $plan (py-call (topic_worker.prepare))
   (if (== (py-call (topic_worker.field $plan "ready")) 1)
     (let* (($url (py-call (topic_worker.field $plan "url")))
            ($headers (py-call (topic_worker.field $plan "headers")))
            ($body (py-call (topic_worker.field $plan "body")))
            ($timeout (py-call (topic_worker.field $plan "timeout")))
            ($cap (py-call (topic_worker.field $plan "cap")))
            ($outcome (catch (io:submit (http:request "POST" $url $headers $body $timeout $cap)))))
       (topic-worker:after-submit $plan $outcome)) 0))
