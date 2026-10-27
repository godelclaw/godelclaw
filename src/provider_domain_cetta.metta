; Native provider facts: attempt authority, usage and completion.
; This is an inactive application slice. No SQL, clock, process, provider or
; retry effects occur here. Submitted-attempt facts come from the trusted
; transport owner. Opaque diagnostic JSON never grants ownership.
!(import! &self ./knowledge_store_cetta.metta)
!(import! &self ./usage_evidence_cetta.metta)
!(import! &self (library math))

; Reuse the existing pure provider-counter normalization at the boundary.
; Its complete normalized evidence is returned for the command's provenance.
(= (provider-domain:from-count $count)
  (case $count ((JsonNull ProviderCountUnknownV2) ($known (ProviderCountV2 $known)))))
(= (provider-domain:usage-evidence $raw)
  (let* (($evidence (ue:evidence $raw)) ($counts (ue:field $evidence "counts" (JsonObject ())))
         ($reported (ue:field $evidence "reported_usage" (JsonObject ())))
         ($input (provider-domain:from-count (ue:field $counts "input_tokens" JsonNull)))
         ($output (provider-domain:from-count (ue:field $counts "output_tokens" JsonNull)))
         ($cached (provider-domain:from-count (ue:field $counts "cached_tokens" JsonNull)))
         ($written (provider-domain:from-count (ue:field $reported "cache_creation_input_tokens" JsonNull))))
    (ProviderUsageObservationV2 (ProviderUsageV2 $input $output $cached $written) $evidence)))

(= (provider-domain:id? $identity)
  (if (== (get-type $identity) String) (> (str:byte-length $identity) 0) False))
(= (provider-domain:keys $command)
  (case $command
    (((ProviderFinishV2 $call $attempt $reference $usage $source)
      (provider-domain:keys-ready $call $attempt True))
     ((ProviderRecordUsageV2 $call $attempt $usage $source)
      (provider-domain:keys-ready $call $attempt False))
     ($other (ProviderDomainRefusedV2 command-format)))))
(= (provider-domain:keys-ready $call $attempt $completion)
  (if (provider-domain:id? $call)
    (if (provider-domain:id? $attempt)
      (let $common ((ProviderCallKeyV2 $call) (ProviderAttemptKeyV2 $call $attempt)
                    (ProviderUsageKeyV2 $call $attempt))
        (if $completion
          (ProviderDomainKeysV2 (cons-atom (ProviderLeaseKeyV2 $call)
            (cons-atom (ProviderAnswerKeyV2 $call) $common)))
          (ProviderDomainKeysV2 $common)))
      (ProviderDomainRefusedV2 attempt-identity))
    (ProviderDomainRefusedV2 call-identity)))

; Normalized counts are nonnegative integers or explicit unknowns. There is
; no float/text affinity, missing-as-zero conversion or signed64 restriction.
(= (provider-domain:counter? $value)
  (case $value
    ((ProviderCountUnknownV2 True)
     ((ProviderCountV2 $count)
      (if (== (get-metatype $count) Expression) False
        (case (math:integer-divmod $count 1)
          (((IntegerDivmodV1 $integer 0) (>= $integer 0))
           ($other False)))))
     ($other False))))
(= (provider-domain:usage? $usage)
  (case $usage
    (((ProviderUsageV2 $input $output $cached $written)
      (if (provider-domain:counter? $input)
        (if (provider-domain:counter? $output)
          (if (provider-domain:counter? $cached) (provider-domain:counter? $written) False) False) False))
     ($other False))))

(= (provider-domain:merge-counter $old $incoming)
  (case ($old $incoming)
    (((ProviderCountUnknownV2 $new) (ProviderCounterMergedV2 $new))
     (($old ProviderCountUnknownV2) (ProviderCounterMergedV2 $old))
     (((ProviderCountV2 $a) (ProviderCountV2 $b))
      (if (== $a $b) (ProviderCounterMergedV2 (ProviderCountV2 $a)) ProviderCounterConflictV2))
     ($other ProviderCounterConflictV2))))
(= (provider-domain:merge-usage
      (ProviderUsageV2 $a $b $c $d) (ProviderUsageV2 $w $x $y $z))
  (let* (($input (provider-domain:merge-counter $a $w))
         ($output (provider-domain:merge-counter $b $x))
         ($cached (provider-domain:merge-counter $c $y))
         ($written (provider-domain:merge-counter $d $z)))
    (case ($input $output $cached $written)
      ((((ProviderCounterMergedV2 $i) (ProviderCounterMergedV2 $o)
         (ProviderCounterMergedV2 $r) (ProviderCounterMergedV2 $s))
        (ProviderUsageMergedV2 (ProviderUsageV2 $i $o $r $s)))
       ($other ProviderUsageConflictV2)))))

(= (provider-domain:usage-plan $call $attempt $old $incoming)
  (case $old
    ((KnowledgeAbsentV1
      (ProviderUsagePlanV2 ProviderUsageAcceptedV2
        ((KnowledgePutV1 (ProviderUsageKeyV2 $call $attempt) $incoming))))
     ((KnowledgePresentV1 $before)
      (if (provider-domain:usage? $before)
        (case (provider-domain:merge-usage $before $incoming)
          (((ProviderUsageMergedV2 $merged)
            (case (kb:same $before $merged)
              (((KnowledgeEqualV1 True) (ProviderUsagePlanV2 ProviderUsageAcceptedV2 ()))
               ((KnowledgeEqualV1 False)
                (ProviderUsagePlanV2 ProviderUsageAcceptedV2
                  ((KnowledgePutV1 (ProviderUsageKeyV2 $call $attempt) $merged))))
               ($error $error))))
           (ProviderUsageConflictV2 (ProviderUsagePlanV2 ProviderUsageConflictV2 ()))))
        (ProviderDomainRefusedV2 stored-usage-format)))
     (KnowledgeUnobservedV1 (ProviderDomainRefusedV2 usage-not-observed))
     ($other (ProviderDomainRefusedV2 usage-observation-format)))))

(= (provider-domain:plan $command $points)
  (case $command
    (((ProviderFinishV2 $call $attempt $reference $usage $source)
      (provider-domain:plan-context $call $attempt $usage $points $command))
     ((ProviderRecordUsageV2 $call $attempt $usage $source)
      (provider-domain:plan-context $call $attempt $usage $points $command))
     ($other (ProviderDomainRefusedV2 command-format)))))
(= (provider-domain:plan-context $call $attempt $usage $points $command)
  (case (provider-domain:keys $command)
    (((ProviderDomainKeysV2 $identities)
      (if (provider-domain:usage? $usage)
        (provider-domain:plan-call $call $attempt $usage $points $command
          (kb:lookup $points (ProviderCallKeyV2 $call)))
        (ProviderDomainRefusedV2 usage-format)))
     ($error $error))))
(= (provider-domain:plan-call $call $attempt $usage $points $command $info)
  (case $info
    (((KnowledgePresentV1 (ProviderCallInfoV2 $provider $model $purpose $origin))
      (provider-domain:plan-attempt $call $attempt $usage $points $command
        (kb:lookup $points (ProviderAttemptKeyV2 $call $attempt))))
     (KnowledgeUnobservedV1 (ProviderDomainRefusedV2 call-not-observed))
     (KnowledgeAbsentV1 (ProviderDomainRefusedV2 unregistered-call))
     ($other (ProviderDomainRefusedV2 stored-call-format)))))
(= (provider-domain:plan-attempt $call $attempt $usage $points $command $registered)
  (case $registered
    (((KnowledgePresentV1 (ProviderSubmittedAttemptV2 $transport))
      (case (provider-domain:usage-plan $call $attempt
              (kb:lookup $points (ProviderUsageKeyV2 $call $attempt)) $usage)
        (((ProviderUsagePlanV2 $status $changes)
          (provider-domain:decide $command $points $status $changes))
         ($error $error))))
     (KnowledgeUnobservedV1 (ProviderDomainRefusedV2 attempt-not-observed))
     (KnowledgeAbsentV1 (ProviderDomainRefusedV2 unregistered-attempt))
     ($other (ProviderDomainRefusedV2 submitted-attempt-required)))))

(= (provider-domain:decide (ProviderRecordUsageV2 $call $attempt $usage $source)
      $points $status $changes)
  (ProviderDomainPlanV2 (ProviderUsageRecordedV2 $status) $changes))
(= (provider-domain:decide (ProviderFinishV2 $call $attempt $reference $usage $source)
      $points $status $changes)
  (let* (($lease (kb:lookup $points (ProviderLeaseKeyV2 $call)))
         ($answer (kb:lookup $points (ProviderAnswerKeyV2 $call))))
    (case $lease
      (((KnowledgePresentV1 (ProviderLeaseV2 $owner $phase))
        (if (== $attempt $owner)
          (case $phase
            ((ProviderLeaseActiveV2
              (provider-domain:finish-owned $call $attempt $reference $answer $status $changes False))
             (ProviderLeaseCompletedV2
              (provider-domain:finish-owned $call $attempt $reference $answer $status $changes True))
             (ProviderLeaseUnknownV2 (ProviderDomainPlanV2 (ProviderNotOwnedV2 $status) $changes))
             (ProviderLeaseFailedV2 (ProviderDomainPlanV2 (ProviderNotOwnedV2 $status) $changes))
             ($other (ProviderDomainRefusedV2 stored-lease-format))))
          (ProviderDomainPlanV2 (ProviderNotOwnedV2 $status) $changes)))
       (KnowledgeAbsentV1 (ProviderDomainPlanV2 (ProviderNotOwnedV2 $status) $changes))
       (KnowledgeUnobservedV1 (ProviderDomainRefusedV2 lease-not-observed))
       ($other (ProviderDomainRefusedV2 stored-lease-format))))))

; Conflicting usage remains in the immutable command receipt for reconciliation.
; It does not throw away a received answer or overwrite an established count.
(= (provider-domain:finish-owned $call $attempt $reference $before $status $changes $completed)
  (case $before
    ((KnowledgeAbsentV1
      (if $completed (ProviderDomainRefusedV2 completed-answer-missing)
        (provider-domain:completion-plan $call $attempt $reference $status $changes)))
     ((KnowledgePresentV1 (ProviderAnswerV2 $owner $original))
      (if (== $owner $attempt)
        (case (kb:same $reference $original)
          (((KnowledgeEqualV1 True)
            (if $completed (ProviderDomainPlanV2 (ProviderAlreadyCompletedV2 $status) $changes)
              (provider-domain:completion-plan $call $attempt $reference $status $changes)))
           ((KnowledgeEqualV1 False) (ProviderDomainPlanV2 (ProviderAnswerConflictV2 $status) $changes))
           ($error $error)))
        (ProviderDomainPlanV2 (ProviderAnswerConflictV2 $status) $changes)))
     (KnowledgeUnobservedV1 (ProviderDomainRefusedV2 answer-not-observed))
     ($other (ProviderDomainRefusedV2 stored-answer-format)))))
(= (provider-domain:completion-plan $call $attempt $reference $status $changes)
  (let $all (cons-atom (KnowledgePutV1 (ProviderLeaseKeyV2 $call)
                         (ProviderLeaseV2 $attempt ProviderLeaseCompletedV2))
              (cons-atom (KnowledgePutV1 (ProviderAnswerKeyV2 $call) (ProviderAnswerV2 $attempt $reference)) $changes))
    (ProviderDomainPlanV2 (ProviderCompletedV2 $status) $all)))

; A newly committed observation is not necessarily a new completion. The
; transport continuation must also own its publication/effect receipt.
(= (provider-domain:first-completion? $reply)
  (case $reply
    (((KnowledgeCommittedV1 (ProviderCompletedV2 $status) $epoch $revision) True)
     ($other False))))

; Check replay before planning: a completed command remains replayable after
; its lease or other facts change. A new command retains the captured view
; through its complete point observations and one scoped publication.
(= (provider-domain:execute $owner $view $receipt $command $maximum-bytes)
  (case (kb:receipt $view $receipt $command $maximum-bytes)
    ((KnowledgeReceiptAbsentV1
      (case (provider-domain:keys $command)
        (((ProviderDomainKeysV2 $identities)
          (case (kb:observe $view $identities $maximum-bytes)
            (((KnowledgeObservedV1 $points $reads)
              (case (provider-domain:plan $command $points)
                (((ProviderDomainPlanV2 $answer $changes)
                  (kb:publish $owner $view $reads $receipt $command $answer $changes $maximum-bytes))
                 ((ProviderDomainRefusedV2 $why)
                  (kb:publish $owner $view $reads $receipt $command (ProviderDomainRefusedV2 $why) () $maximum-bytes))
                 ($error $error))))
             ($error $error))))
         ($error $error))))
     ($replay-or-error $replay-or-error))))
