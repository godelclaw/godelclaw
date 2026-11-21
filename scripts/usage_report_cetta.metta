!(import! &self ../src/usage_report_cetta.metta)
!(let $answers (collapse (catch (ur:main (system:args))))
   (case $answers ((($code) (if (== (get-type $code) Number) (system:exit $code) (system:exit 1))) ($bad (system:exit 1)))))
