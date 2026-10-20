!(import! &self ./src/goals.py)

(= (assertEqual $actual $expected) (assert (== $actual $expected)))

!(let* (($first (py-call (goals.human_wake "batch-17")))
        ($_ (assertEqual (py-call (operator.getitem $first "fresh")) 1))
        ($same (py-call (goals.human_wake "batch-17")))
        ($_ (assertEqual (py-call (operator.getitem $same "fresh")) 0))
        ($next (py-call (goals.human_wake "batch-18")))
        ($_ (assertEqual (py-call (operator.getitem $next "fresh")) 1)))
   (println! HUMAN_WAKE_IDENTITY_OK))
