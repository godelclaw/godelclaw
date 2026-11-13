; Unit test of the coding session launch in src/coding_work.metta: the
; session's engine runs with the session engine flags, whose value allowance
; is the one run.sh gives the agent and its ledger.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/coding_work.metta)
!(let* (($command (work:launch-command "work-x" "/runtime dir" "/bin/cetta" "env A=1" "/sessions/work-x"))
        ($_ (nt:require session-engine-has-the-flags
              (nt:contains $command "'/bin/cetta' '--value-bytes' '268435456' --lang petta --import-mode ancestor-walk coding_main.metta -- '/sessions/work-x'")))
        ($_ (nt:require session-flags (nt:equal (session:engine-flags) ("--value-bytes" "268435456"))))
        ($_ (nt:require agent-launcher-allowance (nt:contains (fs:read-text "run.sh") "--value-bytes 268435456"))))
   (println! UNIT_CODING_LAUNCH_OK))
