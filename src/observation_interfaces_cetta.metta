; Observation adapters preserve the native interface's own completion contract.
!(import! &self ./scoped_observation_cetta.metta)
!(import! &self (library re))

(: obs:syntax (-> Atom %Undefined%))
; Inspectable syntax is not an attempted computation. Its encoding is inert.
(= (obs:syntax $term) (ObservationSyntaxV1 (obs:encode $term)))
(= (obs:undemanded $index) (obs:not-observed $index))

; Native regex find is a completed search or an explicit failure. A successful
; zero-consumption match is one occurrence, not an absent result. Pattern
; descriptions and subject text are captured together before invoking find.
(= (obs:regex-find $pattern $text)
  (let* (($query (obs:encode $pattern)) ($subject (obs:encode $text))
         ($scope (ObservationTextIndexV1 $subject $query NativeRegexFindV1 (system:uuid4-hex))))
    (obs:regex-answers $scope (collapse (re:find $pattern $text)))))
(= (obs:regex-answers $scope $answers)
  (case $answers
    (((re:no-match) (ObservationReportV1 $scope () ObservationClosedV1 ($scope)))
     (((re:match $start $end $captures))
      (let $encoded (obs:encode (RegexOccurrenceV1 $start $end $captures))
        (ObservationReportV1 $scope
          ((ObservationWitnessV1 (RegexMatchV1 $start $end) (ObservationCav1V1 $encoded)))
          ObservationClosedV1 ())))
     (((re:failed $code $detail)) (obs:fault $scope regex $code $detail))
     ($bad (obs:fault $scope regex unexpected-native-outcome $bad)))))

; This is an inspectable proof grade, not a credential or a proof checker.
; A consuming proof system must discharge these exact indexed premises.
(= (obs:negative-grade $reports)
  (if (== $reports ()) (ObservationGradeV1 ())
    (case (obs:status (car-atom $reports))
      (((Empty $scope)
        (case (obs:negative-grade (cdr-atom $reports))
          (((ObservationGradeV1 $rest)
            (let $all (obs:append (obs:dependencies (car-atom $reports)) $rest)
              (ObservationGradeV1 $all)))
           ($bad $bad))))
       ($other (ObservationRefusedV1 negative-premise-not-established $other))))))
