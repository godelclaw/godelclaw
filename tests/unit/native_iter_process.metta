; The iter process policy runs natively: no Python executor on its path.
!(import! &self ../native_test_support.metta)
!(let* (($policy (fs:read-text "src/iter_process_policy.metta"))
        ($library (fs:read-text "lib_mettaclaw.metta"))
        ($_ (nt:require iter-policy-calls-no-python (not (nt:contains $policy "py-call"))))
        ($_ (nt:require library-loads-no-python-iter-adapter (not (nt:contains $library "iter_process_adapter.py")))))
   (println! UNIT_NATIVE_ITER_PROCESS_OK))
