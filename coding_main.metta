; Coding mode: run one session folder to its end (see src/coding_session.metta).
!(import! &self (library lib_import))
!(import! &self (library system))
!(import! &self ./src/native_llm.metta)
!(import! &self ./src/coding_session.metta)
; The session folder is the last argument, with or without a preceding --.
; When the session ends, its report is mailed to the requester and, when a
; worklog command is configured, noted there for the public view.
!(let* (($dir (system:arg (- (system:argc) 1)))
        ((CodingFinished $status) (coding:start $dir)))
   (println! (coding:notify $dir $status)))
