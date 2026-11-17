; Unit test of the coding session launch in src/coding_work.metta: the
; session's engine runs with the session engine flags, whose value allowance
; is the one run.sh gives the agent and its ledger.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/coding_work.metta)
!(import! &self ../../src/coding_session.metta)
!(nt:require unknown-capacity-uses-coding-fallback (== (coding:capacity-value "") 200000))
!(nt:require known-zero-capacity-remains-zero (== (coding:capacity-value 0) 0))
!(nt:require known-capacity-remains-numeric (== (coding:capacity-value 491520) 491520))
!(nt:require capacity-data-is-never-executed (== (coding:capacity-value (Error probe "data")) 200000))
!(nt:require zero-outcomes-is-not-a-completion (== (coding:start-observation ()) (CodingUnestablished ())))
!(nt:require one-unit-is-a-distinct-invalid-completion (== (coding:start-observation (())) (CodingUnestablished (()))))
!(nt:require only-one-terminal-result-is-established (== (coding:start-observation ((CodingFinished done))) (CodingFinished done)))
!(nt:require mixed-result-evidence-is-retained
   (== (coding:start-observation ((CodingFinished done) (Error probe detail)))
       (CodingUnestablished ((CodingFinished done) (Error probe detail)))))
!(let* (($command (work:launch-command "work-x" "/runtime dir" "/bin/cetta" "/sessions/work-x"))
        ($_ (nt:require session-engine-has-the-flags
              (nt:contains $command "'/bin/cetta' '--profile' 'extended' '--value-bytes' '268435456' --lang petta --import-mode ancestor-walk coding_main.metta -- '/sessions/work-x'")))
        ($_ (nt:require session-flags (nt:equal (session:engine-flags) ("--profile" "extended" "--value-bytes" "268435456"))))
        ($_ (nt:require agent-launcher-allowance (nt:contains (fs:read-text "run.sh") "--value-bytes 268435456"))))
   (println! UNIT_CODING_LAUNCH_OK))
