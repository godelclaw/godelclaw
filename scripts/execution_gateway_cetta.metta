!(import! &self ../src/execution_gateway/host_cetta.metta)
!(let* (($flag (system:arg 0)) ($path (system:arg 1))
         ($answers (collapse (catch (if (== (ngq:text $flag) "--config") (ng:main (ngq:text $path)) 78)))))
   (case $answers
     ((($status) (if (== (get-type $status) Number) (system:exit $status) (system:exit 1)))
      ($incomplete (progn (system:write stderr "Gateway stopped without a complete host result; durable claims remain for recovery.\n")
                         (system:exit 1))))))
