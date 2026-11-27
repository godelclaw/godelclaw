; A native evaluator failure: an uncaught PeTTa error ends the program with
; status 2 before anything after it runs.
!(+ 1 "a")
!(println! SHOULD_NOT_RUN)
