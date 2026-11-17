; Coding mode: run one session folder to its end (see src/coding_session.metta).
!(import! &self (library lib_import))
!(import! &self (library system))
!(import! &self ./src/native_llm.metta)
!(import! &self ./src/coding_session.metta)
!(import! &self ./src/coding_io_cetta.metta)
; The session folder is the last argument, with or without a preceding --.
; The single-use run token must be consumed under .ending.lock before any
; stream event, state write, provider call or tool execution. When the session
; ends, its report is mailed to the requester and, when a worklog command is
; configured, noted there for the public view.
!(let* (($dir (system:arg (- (system:argc) 1)))
        ($token (coding:run-token))
        ($claimed (cio:consume-run $dir $token (process:owner) (system:cwd))))
   (if (== $claimed 1)
     (let $observed (coding:start-observation (collapse (catch (coding:start $dir))))
       (case $observed
         (((CodingFinished $status) (println! (coding:notify $dir $status)))
          ((CodingUnestablished $evidence)
           (case (coding:unexpected-start $dir $evidence)
             (((CodingFinished $failed) (println! (coding:notify $dir $failed)))
              ($superseded (println! $superseded)))))
          ($aborted (println! $aborted)))))
     (println! (CodingSuperseded $token))))
