; Faithful control port of patham9/iter f4064d97849ecaccac7939315a3f1a68de15c3ef.
; Host values are boxed JSON; the provider/tools retain their upstream protocol.
!(import! &self ./host_cetta.metta)
!(import! &self ../../src/native_llm.metta)
; Host Booleans cross py-call as the text "true"/"false"; on CeTTa that text
; is a String, which `if` reads as false. Conditions read it as a Boolean.
(= (iter:true $value) (case $value ((True True) (true True) ("true" True) ($other False))))
!(import! &self (library proc))

(= (iter:prune $history $i $limit)
   (if (< $i $limit)
       (progn (ih:cleanup-message (ih:get $history $i))
              (iter:prune $history (+ $i 1) $limit)) True))
(= (iter:drop-leading-tools $history)
   (if (> (ih:size $history) 0)
       (if (iter:true (ih:equal (ih:get (ih:get $history 0) "role") "tool"))
           (iter:drop-leading-tools (ih:drop-head $history)) $history)
       $history))
(= (iter:clean $history $bucket)
   (let* (($current (math:floor (/ (ih:size $history) 20)))
          ($_ (if (> $current $bucket)
                  (iter:prune $history 0 (- (ih:size $history) 10)) True))
          ($pruned (if (>= (ih:size $history) 100)
                       (ih:tail $history 80) $history))
          ($next (if (>= (ih:size $history) 100)
                     4 (max $current $bucket))))
     (iter:cleaned (iter:drop-leading-tools $pruned) $next)))

(= (iter:slow $seconds)
   (if (== $seconds 0) ""
       (progn (ih:sleep 1)
              (let $event (ih:receive)
                (if (iter:true (ih:not-equal $event "")) $event (iter:slow (- $seconds 1)))))))

(= (iter:temporary $event $new $post)
   (if (iter:true (ih:not-equal $event "")) (ih:empty-list)
       (let $message
         (if $new
             "[TASK COMPLETED. DO NOT RE-SEND THE COMPLETED RESPONSE BUT SEND IN CASE YOU FORGOT. NOW QUERY FOR AND PICK A TASK BASED ON YOUR GOALS, PREFERABLY MEMORY CONSOLIDATION: FINDING EPISODES WHICH SUPPORT / CONTRADICT LTM ITEMS, LINKING EPISODES, PROMOTING USEFUL MEMORIES]"
             (if $post
                 "[NO NEW USER INPUT. CONTINUE AUTONOMOUS WORK. DO NOT REPEAT THE PREVIOUS RESPONSE. ONLY USE send FOR GENUINELY NEW INFORMATION OR WHEN USER INPUT IS NEEDED.]"
                 "[NO ADDITIONAL USER INPUT. CONTINUE THE CURRENT USER TASK.]"))
         (ih:append (ih:empty-list)
                    (ih:message "user" (ih:step $message))))))

(= (iter:request $history $base $retry)
   (let* (($temporary (ih:take $base (ih:size $base)))
          ($_ (if (iter:true (ih:not-equal $retry ""))
                  (ih:append $temporary (ih:message "user" $retry)) True))
          ($tools (ih:tools))
          ($errors (ih:get $tools "errors"))
          ($_ (if (iter:true (ih:not-equal $errors ""))
                  (ih:append $temporary (ih:message "user" $errors)) True))
          ($omitted (ih:get $tools "omitted"))
          ($_ (if (> $omitted 0)
                  (ih:append $temporary (ih:message "user" (ih:tool-limit $omitted))) True))
          ($memory (ih:memory))
          ($length (ih:get $memory "length"))
          ($_ (if (> $length 3000)
                  (ih:append $temporary (ih:message "user" (ih:memory-overflow $length))) True))
          ($surface (ih:memory-surface $memory (<= $length 3000)))
          ($response (iter:structured $history $temporary $tools $surface)))
     (if (turn:failed? $response) $response (iter:answer $history $base $response $tools))))
; A reply without a tool call is asked again, as upstream asks.
(= (iter:answer $history $base $response $tools)
   (let* (($message (ih:get $response "message"))
          ($content (ih:optional $message "content"))
          ($_ (if (iter:true (ih:truth $content))
                  (ih:put $message "content" (ih:concat $content "\n[NOT DELIVERED TO ANY CHANNEL. IF THIS WAS INTENDED AS COMMUNICATION, USE send.]")) True))
          ($calls (ih:optional $message "tool_calls")))
     (if (iter:true (ih:truth $calls))
         (progn (ih:put $message "tool_calls" (ih:take $calls 10))
                (iter:response $message $tools))
         (iter:request $history $base
            (if (iter:true (ih:equal (ih:get $response "finish") "length"))
                "[OUTPUT TOKEN LIMIT REACHED. CALL THE REQUIRED TOOL CONCISELY.]"
                (ih:retry-no-tool (ih:optional $message "content")))))))

; The native provider call. Its request and reply cross the host as JSON
; files, so model text never becomes an interned symbol.
(= (iter:structured $history $temporary $tools $surface)
   (ih:provider $history $temporary $tools $surface))

(= (iter:dynamic $tools $name $arguments)
   (ih:invoke $tools $name $arguments))
; As upstream Iter (iter.py:306-316): a tool's failure, raised or returned,
; is its output, and its output is the text of what it returned; the step
; goes on either way. A failure whose effect may have happened (Incomplete,
; such as a worker that died) is said to be uncertain, not failed, so that it
; is not simply done again. An ordinary value is its text, whatever it says.
(= (iter:tool-run $tools $name $arguments)
   (iter:tool-outcome (collapse (catch (iter:dynamic $tools $name $arguments)))))
(= (iter:tool-outcome $answers)
   (case $answers
     ((() "Tool execution failed: no result")
      (((Error $where (Incomplete $why)))
        (str:concat "Tool outcome uncertain: " (diagnostic:text (Error $where (Incomplete $why)) 600)))
      (((Error $where $why)) (str:concat "Tool execution failed: " (diagnostic:text (Error $where $why) 600)))
      (($result) (if (== (get-type $result) String) $result (repr $result)))
      ($results (repr $results)))))
; Consecutive failed steps: upstream waits one second and tries again, which
; repeats a deterministic failure, and its provider call, without end. After
; three in a row each wait doubles from 15 seconds up to ten minutes. The wait
; passes the mode gate and ends early on new input, which the next step takes;
; a step that succeeds starts the count again.
!(bind! &iter-failures (new-state 0))
(= (iter:failure-wait $count)
   (if (<= $count 3) 1 (min 600 (* 15 (iter:power-of-two (- $count 4))))))
(= (iter:power-of-two $n) (if (<= $n 0) 1 (* 2 (iter:power-of-two (- $n 1)))))
(= (iter:dispatch $tools $call)
   (let* (($function (ih:get $call "function"))
          ($name (ih:get $function "name"))
          ($decoded (ih:decode (ih:get $function "arguments"))))
     (if (iter:true (ih:flag $decoded "ok"))
         (if (iter:true (ih:has-tool $tools $name))
             (if (iter:true (ih:flag $decoded "object"))
                 (iter:tool-run $tools $name (ih:get $decoded "value"))
                 "Tool arguments must be a JSON object")
             (ih:unknown $name))
         (ih:get $decoded "error"))))
(= (iter:dispatch-all $history $tools $calls $i $nop)
   (if (>= $i (ih:size $calls)) $nop
       (let* (($call (ih:get $calls $i))
              ($name (ih:get (ih:get $call "function") "name"))
              ($output (ih:clip (ih:stringify (iter:dispatch $tools $call))))
              ($_ (ih:append $history (ih:tool-message $call $output))))
         (iter:dispatch-all $history $tools $calls (+ $i 1) (or $nop (iter:true (ih:equal $name "nop")))))))

; A failed provider call is the step's failure (iter:loop).
(= (iter:act $history $base $event $post $steps $bucket)
   (case (iter:request $history $base "")
     (((iter:response $message $tools) (iter:acted $history $message $tools $event $post $steps $bucket))
      ($failed $failed))))
(= (iter:acted $history $message $tools $event $post $steps $bucket)
   (let* (($_ (ih:append $history $message))
          ($nop (iter:dispatch-all $history $tools (ih:get $message "tool_calls") 0 False))
          ($_ (change-state! &iter-checkpoint (ih:size $history)))
          ($_ (ih:save $history))
          ($used (if (iter:true (ih:not-equal $event "")) 0 (+ $steps 1)))
          ($_ (change-state! &iter-control ($post $used False ""))))
     (if $nop (progn (change-state! &iter-control ($post 0 True ""))
                     (iter:state $history $post 0 True (iter:slow 10) $bucket))
         (if (>= $used 50) (progn (change-state! &iter-control ($post 0 False ""))
                                 (iter:state $history $post 0 False (iter:slow 10) $bucket))
             (iter:state $history $post $used False "" $bucket)))))

(= (iter:cycle $history $post $steps $new $pending $bucket)
   (let* (($_ (ih:sleep 0))
          ($event (if (iter:true (ih:not-equal $pending "")) $pending (ih:receive)))
          ($post-next (if (iter:true (ih:not-equal $event "")) False (if $new True $post)))
          ($steps-next (if (iter:true (ih:not-equal $event "")) 0 $steps))
          ($_ (change-state! &iter-control ($post-next $steps-next False $pending)))
          ($base (iter:temporary $event $new $post))
          ($_ (if (iter:true (ih:not-equal $event ""))
                  (progn (ih:append $history (ih:message "user" (ih:step $event)))
                         (ih:save $history)) True))
          ($_ (change-state! &iter-checkpoint (ih:size $history)))
          ($_ (change-state! &iter-control ($post-next $steps-next False ""))))
     (iter:act $history $base $event $post-next $steps-next $bucket)))

(= (iter:loop (iter:state $history $post $steps $new $pending $bucket))
   (progn (ih:observe $history $post $steps $new $pending $bucket)
          (let (iter:cleaned $clean $next-bucket) (iter:clean $history $bucket)
            (progn (change-state! &iter-checkpoint (ih:size $clean))
                   (change-state! &iter-control ($post $steps $new $pending))
                   (case (catch (iter:cycle $clean $post $steps $new $pending $next-bucket))
                     ; As upstream (iter.py:333): the failed step is reported.
                     (((Error $a $b)
                       (let* (($count (+ (get-state &iter-failures) 1)) ($_ (change-state! &iter-failures $count))
                              ($wait (iter:failure-wait $count))
                              ($_ (system:write stderr (str:join "" ("Output> " (diagnostic:text (Error $a $b) 600)
                                    " [failed step " (repr $count) "; next attempt in " (repr $wait) " s or on new input]\n"))))
                              (($p $s $n $e) (get-state &iter-control))
                              ($arrived (if (iter:true (ih:not-equal $e "")) (let $_ (ih:sleep $wait) $e) (iter:slow $wait))))
                         (iter:loop (iter:state (ih:take $clean (get-state &iter-checkpoint)) $p $s $n $arrived $next-bucket))))
                      ($next (let $_ (change-state! &iter-failures 0) (iter:loop $next)))))))))
(= (iter:start)
   (let $history (ih:initialize)
     (iter:loop (iter:state $history False 0 True ""
                  (math:floor (/ (ih:size $history) 20))))))
