!(import! &self ../src/config_ctl_cetta.metta)
!(let $answers (collapse (catch (cctl:main (system:args))))
   (case $answers ((($code) (if (== (get-type $code) Number) (system:exit $code) (system:exit 2))) ($bad (system:exit 2)))))
