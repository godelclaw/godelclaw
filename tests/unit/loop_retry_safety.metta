; Retry pacing and prompt logging in the loop's source: the prompt body never
; reaches the normal log (only its length and digest do), and a provider retry
; waits on its own backoff, apart from the channel's sleep.
!(import! &self ../native_test_support.metta)

(= (rs:after $source $start $extra)
   (case (str:find $source $start)
     (((TextNotFound $_) "")
      ($from (str:byte-slice $source $from (min (str:byte-length $source) (+ $from $extra)))))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY"))
         ($loop (fs:read-text (path:join $repo "src/loop.metta")))
         ($additions (fs:read-text (path:join $repo "src/turn_additions.metta")))
         ($_ (nt:require the-prompt-body-is-not-logged
               (and (not (nt:contains $loop "(CHARS_SENT: (string_length $send) $send)"))
                    (and (nt:contains $loop "(addition-request-observation $send)")
                         (nt:contains $additions "(CHARS_SENT: (string_length $ready) sha256 (str:sha256 $ready))")))))
         ($block (rs:after $loop "($retryWait" 1500))
         ($_ (nt:require a-provider-retry-waits-on-its-own-backoff
               (and (nt:contains $loop "(runtime-support:wait-for-retry $retryWait)")
                    (and (nt:contains $block "(> $retryWait 0)")
                         (and (nt:contains $block "($waitResult")
                              (nt:contains $block "(channel:sleep-until-message $wait $enabled)")))))))
   (println! UNIT_LOOP_RETRY_SAFETY_OK))
