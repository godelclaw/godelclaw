; Opt-in observation reports beside the unchanged knowledge interface.
!(import! &self ./knowledge_store_cetta.metta)
!(import! &self ./scoped_observation_cetta.metta)

; Stored facts and observations are data, including error-shaped payloads.
(: KnowledgeFactV1 (-> Atom Atom Atom))
(: KnowledgePresentV1 (-> Atom KnowledgeObservation))

(= (kb:read-report $view $identity $maximum-bytes)
   (kb:read-report-at $view $identity $maximum-bytes (system:uuid4-hex)))
(= (kb:read-report-at $view $identity $maximum-bytes $run)
   (case (kb:fact-address $identity)
     (((KnowledgeAddressV1 $key)
       (let $raw (obs:lookup-as $view $key $maximum-bytes
                    (KnowledgePointObservationV1 (obs:encode $identity)) $run)
         (kb:decode-report $identity $raw)))
      ($error (obs:fault (ObservationUnboundV1 (obs:encode $identity) $run)
                         knowledge invalid-identity $error)))))
(= (kb:decode-report $identity (ObservationReportV1 $scope $events $coverage $dependencies))
   (case $events
     ((() (ObservationReportV1 $scope () $coverage $dependencies))
      (((ObservationWitnessV1 $id (ObservationBytesV1 $bytes)))
       (case (kb:decode-fact $identity $bytes)
         (((KnowledgePresentV1 $value)
           (case (__cetta_lib_store_encode $value)
             (((StoreEncoded $encoded)
               (ObservationReportV1 $scope
                 ((ObservationWitnessV1 $id (ObservationCav1V1 $encoded))) $coverage $dependencies))
              ($error (obs:fault $scope knowledge value-codec $error)))))
          ($error (obs:fault $scope knowledge invalid-fact $error)))))
      ($other (ObservationReportV1 $scope $events $coverage $dependencies)))))

; The live view is deliberately absent from the serializable report.
(= (kb:observe-report $view $identity $maximum-bytes)
   (KnowledgeObservationOnV1 $view (obs:encode $identity) $maximum-bytes
     (kb:read-report $view $identity $maximum-bytes)))
(= (kb:observation-report (KnowledgeObservationOnV1 $view $identity $budget $report)) $report)

; Re-observe the exact key under the retained view. Copying a plausible report
; or a view handle cannot substitute invented facts for this native read.
; Publication still needs its own owner/authority and scoped commit validation.
(= (kb:report-scopes-on $view $observation)
   (case $observation
     (((KnowledgeObservationOnV1 $captured $identity-bytes $budget $report)
       (if (not (== $view $captured)) (KnowledgeRefusedV1 observation-view)
         (case (store:decode $identity-bytes)
           (((StoreDecoded $identity)
             (kb:validate-report $view $identity $budget $report))
            ($other (KnowledgeRefusedV1 observation-identity))))))
      ($other (KnowledgeRefusedV1 bound-observation-required)))))
(= (kb:validate-report $view $identity $budget $report)
   (case (obs:scope $report)
     (((ObservationIndexV1 $domain $epoch $revision $query $region $policy $run)
       (let $actual (kb:read-report-at $view $identity $budget $run)
         (if (and (obs:same $actual $report)
                  (and (== (obs:coverage $actual) ObservationClosedV1)
                       (== (obs:diagnostics $actual) ())))
           (case (kb:fact-address $identity)
             (((KnowledgeAddressV1 $key) (KnowledgeReadScopesV2 (reads (point $key))))
              ($error $error)))
           (KnowledgeRefusedV1 observation-not-established))))
      ($other (KnowledgeRefusedV1 observation-index)))))
