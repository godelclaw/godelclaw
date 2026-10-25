; Run independently of the cognitive process. This observes session scopes and
; retries report delivery; it never calls a model or replays a coding tool.
!(import! &self (library lib_import))
!(import! &self ./src/coding_work.metta)
!(println! (work:reap))
