; The framework's proposal window over a history that a mode's records share.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/action_window_cetta.metta)
!(let* (($home (nt:required-env "HOME")) ($path (path:join $home "history.metta"))
        ($_ (fs:write $path (str:join "" (
          "(\"2026-11-18 20:00:00\" \"\n\" \"HUMAN_MESSAGE: \" \"hello\" \"\n\" ((send \"framework reply\")) \"\n\")\n"
          "(\"2026-11-18 20:01:00\" \"\n\" ((shell \"ls\") (send \"telegram\" \"mode reply\")) \"\n\" RESULTS: (\"SUCCESS, RETURN: secret-result-text\" \"SUCCESS\") \"\n\")\n"))))
        ($window (aw:recent-actions $path 8 12000)))
   (let* (($_ (nt:require framework-turn-shown (nt:contains $window "framework reply")))
          ($_ (nt:require mode-commands-shown (nt:contains $window "(shell \"ls\")")))
          ($_ (nt:require mode-results-left-out (not (nt:contains $window "secret-result-text")))))
     (println! UNIT_HISTORY_RECORDS_OK)))
