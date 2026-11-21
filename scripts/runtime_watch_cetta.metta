!(import! &self ../src/runtime_watch_cetta.metta)
!(let $answers (collapse (catch (rw:main (system:args))))
   (case $answers ((($code) (if (== (get-type $code) Number) (system:exit $code) (system:exit 1))) ($bad (system:exit 1)))))
