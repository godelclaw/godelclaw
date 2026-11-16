!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/iter/core.metta)
!(fs:write "prompt.txt" "Native structured request regression.")
!(nt:require actual-request-preparation-is-one-value
   (== (size-atom (collapse (ih:request-plan (ih:list) (ih:list) (ih:tools) "memory"))) 1))
!(nt:require tagged-provider-values-enter-native-cells
   (== (ih:get (ih:box (JsonObject ((JsonPair "name" (JsonString "nop"))))) "name") "nop"))
!(let* (($xs (ih:box (JsonArray ((JsonObject ((JsonPair "content" "old")))))))
        ($slice (ih:take $xs 1)) ($_ (ih:put (ih:get $slice 0) "content" "new"))
        ($_ (nt:require shallow-slice-keeps-message-identity (== (ih:get (ih:get $xs 0) "content") "new")))
        ($_ (ih:append $xs (ih:box (JsonObject ((JsonPair "content" "new"))))))
        ($_ (nt:require equal-values-keep-two-occurrences (== (ih:size $xs) 2)))
        ($_ (ih:put (ih:get $xs 1) "content" "second"))
        ($_ (nt:require equal-occurrences-have-independent-identity (== (ih:get (ih:get $xs 0) "content") "new")))
        ($echo "(ComponentDeclarationV1 \"Echo\" (\"text\"))\n(= (component:run $arguments) (str:concat \"A:\" (sf:get $arguments \"text\" \"\")))")
        ($_ (fs:write "tools/echo.metta" $echo))
        ($snapshot (component:capture-tools))
        ($_ (nt:require native-schema-discovery (component:has? $snapshot "echo")))
        ($_ (fs:write "tools/echo.metta" "(ComponentDeclarationV1 \"Echo\" (\"text\"))\n(= (component:run $arguments) \"B\")"))
        ($_ (nt:require captured-source-is-immutable
              (== (component:invoke $snapshot "echo" (JsonObject ((JsonPair "text" "ček")))) "A:ček")))
        ($_ (nt:require fresh-capture-sees-edit
              (== (component:invoke (component:capture-tools) "echo" (JsonObject ((JsonPair "text" "ček")))) "B")))
        ($_ (nt:require native-argument-error-is-observable
              (component:failed? (component:invoke $snapshot "echo" (JsonObject ())))))
        ($_ (nt:require explicit-python-can-call-captured-native-tools
              (== (str:trim (component:python "print(nop())" $snapshot)) "SUCCESS")))
        ($_ (nt:require explicit-python-sees-captured-tool-bytes
              (== (str:trim (component:python "print(echo('via-python'))" $snapshot)) "A:via-python")))
        ($transformation "(ComponentDeclarationV1 \"Append a note\" ())\n(= (component:transform-request (JsonArray ((JsonArray $messages) $tools))) (JsonArray ((JsonArray (list:append $messages ((JsonObject ((JsonPair \"role\" \"user\") (JsonPair \"content\" \"added\")))))) $tools)))")
        ($_ (fs:write "transformations/01-note.metta" $transformation))
        ($captured (component:capture-transformations))
        ((ComponentRequestV1 $messages $tools $errors)
          (component:transform $captured (JsonArray ()) (JsonArray ())))
        ($_ (nt:require native-transform-keeps-tools (== $tools (JsonArray ()))))
        ($_ (nt:require native-transform-completes (== $errors "")))
        ($_ (nt:require native-transform-preserves-message-value
              (case $messages (((JsonArray ($item)) (== (sf:get $item "content" "") "added")) ($bad False)))))
        ($_ (fs:write "transformations/02-invalid.metta" "(ComponentDeclarationV1 \"Wrong result\" ())\n(= (component:transform-request $input) \"wrong\")"))
        ((ComponentRequestV1 $after $_ $diagnostic)
          (component:transform (component:capture-transformations) (JsonArray ()) (JsonArray ())))
        ($_ (nt:require invalid-transform-stutters-with-diagnostic (and (== $after $messages) (!= $diagnostic "")))))
   (println! UNIT_NATIVE_COMPONENTS_OK))
