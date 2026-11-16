!(import! &self ../src/console/main_cetta.metta)
!(let $answers (collapse (catch (cc:main (system:args))))
   (case $answers
     ((($code) (if (== (get-type $code) Number) (system:exit $code)
                  (progn (system:write stderr "Console operation did not establish a result.\n") (system:exit 1))))
      ($other (progn (system:write stderr "Console operation incomplete; retained records were preserved.\n") (system:exit 1))))))
