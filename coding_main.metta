; Coding mode: run one session folder to its end (see src/coding_session.metta).
!(import! &self (library lib_import))
!(import! &self (library system))
!(import! &self ./src/native_llm.metta)
!(import! &self ./src/coding_session.metta)
; The session folder is the last argument, with or without a preceding --.
; The single-use run token must be consumed under .ending.lock before any
; stream event, state write, provider call or tool execution. When the session
; ends, its report is mailed to the requester and, when a worklog command is
; configured, noted there for the public view.
!(let* (($dir (system:arg (- (system:argc) 1)))
        ($token (coding:run-token))
        ($claimed (py-call (coding_io.consume_run $dir $token (process:owner) (system:cwd)))))
   (if (== $claimed 1)
     (case (coding:start $dir)
       (((CodingFinished $status) (println! (coding:notify $dir $status)))
        ($aborted (println! $aborted))))
     (println! (CodingSuperseded $token))))
