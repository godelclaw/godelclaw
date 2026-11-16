!(import! &self ../src/runtime_housekeeping_cetta.metta)
!(let $answers (collapse (catch (housekeeping:main (system:args))))
   (case $answers ((($code) (if (== (get-type $code) Number) (system:exit $code) (system:exit 1))) ($bad (system:exit 1)))))
