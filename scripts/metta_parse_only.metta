; The source stays inside a parsed list. Bang forms and imports never execute.
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library system))
!(let $source (fs:read-text (system:arg 1))
   (case (catch (parse (str:join "" ("(\n" $source "\n)"))))
     (((Error $_ $reason) (progn (println! (SYNTAX_ERROR $reason)) (system:exit 1)))
      ($parsed (println! SYNTAX_OK)))))
