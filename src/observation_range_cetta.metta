; Bounded reads reuse owned native views, scans and one-shot work capsules.
!(import! &self ./scoped_observation_cetta.metta)
!(import! &self (library work))
!(bind! &observation-issued-ranges (new-state ()))
(: ObservationRangeStateV1 (-> Atom Atom Atom Atom Atom Atom ObservationRangeState))
(: ObservationStepV1 (-> Atom Atom ObservationStep))

(= (obs:issued-range? $work $items)
  (if (== $items ()) False
    (or (== $work (car-atom $items)) (obs:issued-range? $work (cdr-atom $items)))))
(= (obs:remove-range $work $items)
  (function (if (== $items ()) (return ())
    (let* (($head (car-atom $items)) ($tail (obs:remove-range $work (cdr-atom $items))))
      (if (== $work $head) (return $tail)
        (let $out (cons-atom $head $tail) (return $out)))))))
(= (obs:range-issue $state $limit)
  (let $issued (get-state &observation-issued-ranges)
    (if (>= (size-atom $issued) $limit) (ObservationRefusedV1 cursor-capacity ())
      (let $capsule (work-new $state)
        (case $capsule
          (((NativeHandle "work" $id)
            (let* (($next (cons-atom $capsule $issued))
                   ($discarded1 (change-state! &observation-issued-ranges $next)))
              (ObservationCursorV2 $capsule)))
           ($bad (ObservationRefusedV1 cursor-allocation $bad))))))))
(=
  (obs:open-range $view $lower $upper $maximum-cursors)
  (if (obs:range-natural? $maximum-cursors)
    (let
    $issued
    (get-state &observation-issued-ranges)
    (if
      (or (<= $maximum-cursors 0) (>= (size-atom $issued) $maximum-cursors))
      (ObservationRefusedV1 cursor-capacity ())
      (case
        (collapse (store:view-clone $view))
        ((((StoreSnapshot $owned $epoch $revision))
            (let
              $scope
              (obs:index
                $owned
                (RangeQueryV1 $lower $upper ())
                (reads (range $lower $upper))
                (PortableNativeRangeV1 "raw-bytes")
                (system:uuid4-hex))
              (case
                $scope
                (((ObservationIndexV1 $d $e $r $q $region $policy $run)
                    (case
                      (collapse (store:scan-open $owned $lower $upper))
                      ((((StoreScan $scan))
                          (obs:range-residual $scope $owned $scan 0 () $maximum-cursors budget))
                        ($bad
                          (let
                            $_
                            (collapse (store:close $owned))
                            (ObservationRefusedV1 cursor-open $bad))))))
                  ($bad (let $_ (collapse (store:close $owned)) $bad))))))
          ($bad (ObservationRefusedV1 snapshot $bad))))))
    (ObservationRefusedV1 invalid-cursor-limit ())))
; Opening returns the same report/residual pair used by every step.
(= (obs:range-report $scope $events $coverage)
  (let $dependencies (if (and (== $events ()) (== $coverage ObservationClosedV1)) ($scope) ())
    (ObservationReportV1 $scope $events $coverage $dependencies)))
(= (obs:range-release $view $scan)
  (let* (($discarded2 (collapse (store:close $scan))) ($discarded3 (collapse (store:close $view)))) True))
(= (obs:range-residual $scope $view $scan $position $events $limit $reason)
  (obs:range-residual-as $scope $view $scan $position $events $limit $reason NoObservationNeedV1))
(=
  (obs:range-residual-as $scope $view $scan $position $events $limit $reason $detail)
  (let*
    (($state (ObservationRangeStateV1 $scope $view $scan $position $events $limit))
      ($cursor (obs:range-issue $state $limit)))
    (case
      $cursor
      (((ObservationCursorV2 $work)
          (let
            $report
            (obs:range-report
              $scope
              $events
              (ObservationOpenV1 $reason (ObservationResumeV2 (obs:run-id $scope) $position $detail)))
            (ObservationStepV1 $report $cursor)))
        ($bad (obs:range-error $scope $view $scan $events residual-refused $bad))))))
(= (obs:range-terminal $scope $view $scan $events $coverage)
  (let* (($discarded4 (obs:range-release $view $scan))
         ($report (obs:range-report $scope $events $coverage)))
    (ObservationStepV1 $report NoObservationContinuationV1)))
(= (obs:range-error $scope $view $scan $events $code $detail)
  (let* (($id (RangeFaultV1 (size-atom $events)))
         ($event (ObservationFaultV1 $id (obs:diagnostic range $code $detail)))
         ($next (obs:append $events ($event))))
    (obs:range-terminal $scope $view $scan $next (ObservationOpenV1 fault NoObservationContinuationV1))))
(= (obs:range-natural? $value)
  (let $wire (obs:encode $value)
    (if (== (get-type $wire) String)
      (if (str:starts-with $wire "CAV1I") (>= $value 0) False) False)))
(= (obs:step $cursor $work-budget $maximum-bytes)
  (if (and (obs:range-natural? $work-budget) (obs:range-natural? $maximum-bytes))
    (obs:range-consume $cursor $work-budget $maximum-bytes False)
    (ObservationRefusedV1 invalid-budget ())))
(= (obs:cancel $cursor) (obs:range-consume $cursor 0 0 True))
(= (obs:range-consume $cursor $budget $maximum-bytes $cancel)
  (let $issued (get-state &observation-issued-ranges)
    (case $cursor
      (((ObservationCursorV2 $capsule)
        (if (obs:issued-range? $capsule $issued)
          (let* (($remaining (obs:remove-range $capsule $issued))
                 ($registration-removed (change-state! &observation-issued-ranges $remaining))
                 ($answers (collapse (work-take $capsule 1)))
                 ($capsule-cancelled (collapse (work-cancel $capsule))))
            (obs:range-unbox $answers $budget $maximum-bytes $cancel))
          (obs:range-refused unissued-or-consumed-cursor ())))
       ($bad (obs:range-refused invalid-cursor $bad))))))
(= (obs:range-unbox $answers $budget $maximum-bytes $cancel)
  (case $answers
    ((((taken ((ObservationRangeStateV1 $scope $view $scan $position $events $limit)) complete))
      (if $cancel
        (obs:range-terminal $scope $view $scan $events (ObservationOpenV1 cancelled NoObservationContinuationV1))
        (obs:range-drive $scope $view $scan $position $events $limit $budget $maximum-bytes)))
     ($bad (obs:range-refused invalid-capsule $bad)))))
(= (obs:range-drive $scope $view $scan $position $events $limit $budget $maximum-bytes)
  (if (<= $budget 0) (obs:range-residual $scope $view $scan $position $events $limit budget)
    (case (collapse (store:scan-next $scan $position))
      (((StoreEnd)
        (obs:range-terminal $scope $view $scan $events ObservationClosedV1))
       (((StoreKey $key $bytes $next))
        (obs:range-value $scope $view $scan $position $events $limit $budget $maximum-bytes $key $next
          (collapse (store:get $view $key $maximum-bytes))))
       ($bad (obs:range-error $scope $view $scan $events cursor-failed $bad))))))
(= (obs:range-value $scope $view $scan $position $events $limit $budget $maximum-bytes $key $next $answers)
  (case $answers
    ((((StoreValue $bytes))
      (let* (($event (ObservationWitnessV1 (RangeOccurrenceV1 $key) (ObservationBytesV1 $bytes)))
             ($all (obs:append $events ($event))))
        (obs:range-drive $scope $view $scan $next $all $limit (- $budget 1) $maximum-bytes)))
     (((StoreTooLarge $required))
      (obs:range-residual-as $scope $view $scan $position $events $limit materialization-limit (RequiredBytesV1 $required)))
     (((StoreRefused capacity))
      (obs:range-residual $scope $view $scan $position $events $limit resource-limit))
     ($bad (obs:range-error $scope $view $scan $events value-failed $bad)))))


(= (obs:range-refused $reason $detail)
  (let $report (obs:fault unbound range $reason $detail)
    (ObservationStepV1 $report NoObservationContinuationV1)))
