; Unit tests of src/loop_host_cetta.metta; no Python. The driver sets
; METTACLAW_HEARTBEAT_INTERVAL_SECONDS=" 45 ", METTACLAW_COMMAND_BATCH_LIMIT=0
; and a scratch METTACLAW_HISTORY_PATH whose parent does not exist yet.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/loop_host_cetta.metta)

!(nt:require int-empty-is-default (nt:equal (loop-host:int-text "" 9) 9))
!(nt:require int-plain (nt:equal (loop-host:int-text "12" 9) 12))
!(nt:require int-whitespace (nt:equal (loop-host:int-text " 12\n" 9) 12))
!(nt:require int-signs (nt:equal ((loop-host:int-text "+7" 9) (loop-host:int-text "-3" 9)) (7 -3)))
!(nt:require int-underscores (nt:equal (loop-host:int-text "1_000" 9) 1000))
!(nt:require int-double-underscore-invalid (nt:equal (loop-host:int-text "1__0" 9) 9))
!(nt:require int-fraction-invalid (nt:equal (loop-host:int-text "12.5" 9) 9))
!(nt:require int-hex-invalid (nt:equal (loop-host:int-text "0x10" 9) 9))
!(nt:require int-word-invalid (nt:equal (loop-host:int-text "many" 9) 9))
!(nt:require heartbeat-setting-read (nt:equal (loop-host:heartbeat-interval) 45))
!(nt:require unset-setting-default (nt:equal (loop-host:int-env "GODELCLAW_UNIT_UNSET" 1800) 1800))
!(nt:require nonpositive-batch-limit-keeps-policy (nt:equal (loop-host:command-batch-limit 5) 5))
!(nt:require reasoning-effort-default (nt:equal (loop-host:reasoning-effort) "default"))
!(nt:require empty-text-is-empty (nt:equal (loop-host:nonempty? "") False))
!(nt:require empty-expression-is-empty (nt:equal (loop-host:nonempty? ()) False))
!(nt:require text-is-nonempty (nt:equal (loop-host:nonempty? "x") True))

!(let $path (loop-host:history-path)
   (let* (($_ (nt:require first-append-creates-parent (nt:equal (loop-host:history-append "2026 turn λ") 1)))
          ($_ (nt:require second-append-keeps-newline (nt:equal (loop-host:history-append "next\n") 1)))
          ($_ (nt:require journal-text (nt:equal (fs:read-text $path) "2026 turn λ\nnext\n")))
          ($mode (nt:exit-ok (proc:run ("stat" "-c" "%a" $path) (system:cwd) (nt:environment "/tmp") 5000 1024))))
     (nt:require journal-private (nt:equal $mode "600\n"))))
!(nt:require unwritable-journal-reports-zero
   (nt:equal (loop-host:append-line (path:dirname (loop-host:history-path)) "x") 0))
!(println! UNIT_LOOP_HOST_OK)
