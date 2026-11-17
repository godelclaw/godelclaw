; Import the actual production boundary and require one observed outcome.
!(import! &self ../../src/state_io.metta)
!(let $answers (collapse (catch (turn:context-input "capacity" 32768)))
   (case $answers
     ((($value)
       (let $kind (if (== (get-type $value) Number) "known"
                    (if (== $value "") "unknown" "invalid"))
         (system:write stdout (str:concat (json:stringify-python (JsonObject ((JsonPair "kind" $kind)
           (JsonPair "capacity" (if (== $kind "known") $value JsonNull))))) "\n"))))
      ($bad (progn (println! CAPACITY_OUTCOME_NOT_ESTABLISHED) (system:exit 2))))))
