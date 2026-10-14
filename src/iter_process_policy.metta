;; Transformations are .metta programs.  The host captures their source and
;; gives each one a fresh evaluator child; success replaces the visible request
;; and every other outcome stutters locally.
!(import! &self (library lib_import))
!(import! &self ./src/iter_process_io)

(= (iter-process-visible $system-message $activity-message $advertised)
   (coding-view
     (coding-message-list
       (coding-message system $system-message)
       (coding-message-list
         (coding-message user $activity-message)
         coding-messages-empty))
     (coding-capability-list
       (coding-capability command-batch $advertised)
       coding-capabilities-empty)))

(= (iter-process-prepare-request
      $system-message $activity-message $advertised $run-iter)
   (let $visible
        (iter-process-visible
          $system-message $activity-message $advertised)
        (if (== $run-iter 1)
            (iter-native-run default $visible)
            (iter-native-result $visible iter-observations-empty))))

(= (iter-native-concat3 $left $middle $right)
   (string_concat (string_concat $left $middle) $right))

(= (iter-native-render-message (coding-message $role $content))
   (iter-native-concat3 (swrite $role) ": " (swrite $content)))

(= (iter-native-render-messages coding-messages-empty) "")
(= (iter-native-render-messages
      (coding-message-list $head $tail))
   (let $rendered-head (iter-native-render-message $head)
        (if (== $tail coding-messages-empty)
            $rendered-head
            (iter-native-concat3
              $rendered-head "\n\n"
              (iter-native-render-messages $tail)))))

(= (iter-native-render-observations $observations)
   (if (== $observations iter-observations-empty)
       ""
       (string_concat "ITER_PROCESS_OBSERVATIONS "
                      (swrite $observations))))

(= (iter-process-render-request
      (iter-native-result
        (coding-view $messages $advertised) $observations))
   (let* (($rendered-messages (iter-native-render-messages $messages))
          ($rendered-advertised (swrite $advertised))
          ;; The advertisement changes only when the skills do, so it leads
          ;; the request with the other stable text, ahead of the messages.
          ($base
            (iter-native-concat3
              (string_concat "ADVERTISED_CAPABILITIES:\n" $rendered-advertised)
              "\n\n"
              $rendered-messages))
          ($rendered-observations
            (iter-native-render-observations $observations))
          ($rendered
            (if (== $rendered-observations "")
                $base
                (iter-native-concat3
                  $base "\n\n" $rendered-observations))))
     $rendered))

(= (iter-process-request-observations
      (iter-native-result $visible $observations))
   (iter-native-render-observations $observations))
