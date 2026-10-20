; Observe the pinned utility/skill bodies through the declaration adapter.
!(import! &self ./tests/lib/utils)
!(import! &self ../src/helper.py)
!(import! &self ./src/utils)
!(import! &self ./src/skills)

!(change-state! &compat-predicate-count 0)
(= (compat-predicate)
   (progn
     (change-state! &compat-predicate-count
                    (+ (get-state &compat-predicate-count) 1))
     True))
!(test (empty-to-bool (compat-predicate)) True)
!(test (get-state &compat-predicate-count) 2)

; The second run may be empty even though the first yielded a value.
!(change-state! &compat-predicate-count 0)
(= (compat-once-only)
   (let $prior (get-state &compat-predicate-count)
     (progn (change-state! &compat-predicate-count (+ $prior 1))
            (if (== $prior 0) True (empty)))))
!(test (collapse (empty-to-bool (compat-once-only))) (True False))
!(test (get-state &compat-predicate-count) 2)

; Enumerate all first-run answers, but negation stops at one second-run answer.
!(change-state! &compat-predicate-count 0)
(= (compat-many)
   (let $index (superpose (1 2 3))
     (progn (change-state! &compat-predicate-count
                          (+ (get-state &compat-predicate-count) 1)) True)))
!(test (collapse (empty-to-bool (compat-many))) (True True True))
!(test (get-state &compat-predicate-count) 4)

; v1.0.4's Atom input computes the name; its Expression input holds the call.
(= (compat-name) compat-skill)
!(change-state! &compat-argument-count 0)
(= (compat-arguments)
   (progn (change-state! &compat-argument-count 1) (computed-argument)))
!(add-skill (compat-name) "Legacy input demand" (compat-arguments))
!(test (repr (collapse
         (match &self (dynamic-skill compat-skill $info) $info)))
       "((compat-skill \"Legacy input demand\" (compat-arguments)))")
!(test (get-state &compat-argument-count) 0)
