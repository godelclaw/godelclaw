;; The wait while the operator has stopped cognition, as a process of its own:
;; it exits 0 once cognition is enabled and 3 when a recycle is requested.
;; Run after utils.metta, from the loop's directory.
!(host:pause-child 0)
