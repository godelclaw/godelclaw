;; A structured coding boundary implemented in MeTTa policy space.
;; Iter transformations may replace only the visible messages and
;; advertisements.  Execution and permission stay captured beside them.

(= (coding-visible
      (coding-boundary $messages $advertised $executable $permitted))
   (coding-view $messages $advertised))

(= (coding-install-visible
      (coding-boundary $messages $advertised $executable $permitted)
      (coding-view $next-messages $next-advertised))
   (coding-boundary
     $next-messages $next-advertised $executable $permitted))

(= (coding-boundary-messages
      (coding-boundary $messages $advertised $executable $permitted))
   $messages)
(= (coding-boundary-advertised
      (coding-boundary $messages $advertised $executable $permitted))
   $advertised)
(= (coding-boundary-executable
      (coding-boundary $messages $advertised $executable $permitted))
   $executable)
(= (coding-boundary-permitted
      (coding-boundary $messages $advertised $executable $permitted))
   $permitted)

(= (coding-state-boundary (coding-state $boundary $workspace)) $boundary)
(= (coding-state-workspace (coding-state $boundary $workspace)) $workspace)

;; The real turn path keeps the legacy agent request byte-for-byte while the
;; structured policies use the captured Iter request adapter.  The prepared
;; JSON remains opaque to the loop; these accessors are its only consumers.
(= (coding-prepare-request
      $selected $legacy-request $system-message $activity-message $advertised)
   (if (or (== (policy-structured-request $selected) 1) (== $legacy-request ""))
       (coding-prepared-request
         (iter-process-prepare-request
           $system-message $activity-message $advertised
           (policy-iter-transformations $selected)))
       (coding-legacy-request $legacy-request)))

(= (coding-request-prompt (coding-legacy-request $request)) $request)
(= (coding-request-prompt (coding-prepared-request $prepared-json))
   (iter-process-render-request $prepared-json))

(= (coding-request-observations (coding-legacy-request $request)) "")
(= (coding-request-observations (coding-prepared-request $prepared-json))
   (iter-process-request-observations $prepared-json))

;; Open Iter transformation interface.  A transformation file contributes
;; equations for (coding-transform <name> <visible>).  No name catalogue is
;; built into this runner.
(= (coding-apply-visible $boundary failure) $boundary)
(= (coding-apply-visible $boundary (success $next-visible))
   (coding-install-visible $boundary $next-visible))

(= (coding-run-transform $boundary $name)
   (coding-apply-visible
     $boundary (coding-transform $name (coding-visible $boundary))))

(= (coding-run-transforms $boundary coding-transforms-empty) $boundary)
(= (coding-run-transforms
      $boundary (coding-transform-list $name $rest))
   (coding-run-transforms (coding-run-transform $boundary $name) $rest))

;; Small structural lists keep this file independent of provider and Python
;; container conventions.
(= (coding-message-append-one coding-messages-empty $message)
   (coding-message-list $message coding-messages-empty))
(= (coding-message-append-one
      (coding-message-list $head $tail) $message)
   (coding-message-list
     $head (coding-message-append-one $tail $message)))

(= (coding-capability-member $name coding-capabilities-empty) False)
(= (coding-capability-member
      $name (coding-capability-list $head $tail))
   (if (== $name $head)
       True
       (coding-capability-member $name $tail)))

;; The effect adapter contributes equations for
;; (coding-execute <workspace> <command>).  The call remains below both gates:
;; unknown and denied proposals never invoke it.
(= (coding-broker $boundary $workspace
                  (coding-command $capability $payload))
   (if (coding-capability-member
         $capability (coding-boundary-executable $boundary))
       (if (coding-capability-member
             $capability (coding-boundary-permitted $boundary))
           (case (coding-execute
                   $workspace (coding-command $capability $payload))
             (((execution-success $next-workspace $result)
               (brokered $next-workspace
                 (command-succeeded $capability $result)))
              ((execution-failure $error)
               (brokered $workspace
                 (command-failed $capability $error)))))
           (brokered $workspace (command-denied $capability)))
       (brokered $workspace (command-unknown $capability))))

(= (coding-record $boundary $observation)
   (coding-install-visible
     $boundary
     (coding-view
       (coding-message-append-one
         (coding-boundary-messages $boundary)
         (coding-message tool $observation))
       (coding-boundary-advertised $boundary))))

(= (coding-step
      (coding-state $boundary $workspace) $command)
   (let $outcome (coding-broker $boundary $workspace $command)
     (case $outcome
       (((brokered $next-workspace $observation)
         (coding-state
           (coding-record $boundary $observation) $next-workspace))))))
