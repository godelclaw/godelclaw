!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; Two real turns through the shared PeTTa loop.  The first executes a real
;; deterministic help command.  The second request must contain both that
;; result and the Iter fixture's appended message before its model call.
;; The provider is the test world's loopback peer, on the native transport: its
;; scripted replies and the requests it received are files in the world.
(= (probe:world $name) (str:concat (system:getenv-or-default "NATIVE_TEST_WORLD" "") $name))
(= (probe:reply $content)
   (str:join "" ("{\"id\":\"fixture\",\"type\":\"message\",\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"" $content
                 "\"}],\"stop_reason\":\"end_turn\",\"usage\":{\"input_tokens\":10,\"output_tokens\":2}}")))
(= (probe:has $text $needle)
   (case (str:find $text $needle) (((TextNotFound $missing) False) ($found True))))
(= (probe:coding-ok)
   (let* (($count (json:parse (str:strip-python (fs:read-text (probe:world "count")))))
          ($first (fs:read-text (probe:world "request-1.body")))
          ($second (fs:read-text (probe:world "request-2.body"))))
     (and (>= $count 2)
          (and (probe:has $first "native-iter")
               (and (probe:has $second "LAST_SKILL_USE_RESULTS:")
                    (or (probe:has $second "usage: (send") (probe:has $second "api:help")))))))
!(fs:write (probe:world "response-1.body") (probe:reply "((help \\\"send\\\"))"))
!(fs:write (probe:world "response-2.body") (probe:reply "((nop))"))
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 1))")
!(initMemory)
!(configure commchannel telegram)

!(let* (($_ (py-call (loop_modes.set_mode "coding")))
         ($initial (initLoop))
         ($after-first
          (process-step $initial
            (turnProcess (process-periphery $initial))))
         ($_ (cut))
         ($after-second
          (process-step $after-first
            (turnProcess (process-periphery $after-first))))
         ($_ (cut))
         ($_ (assertEqual (process-root $after-second) weak-process-core-v1))
         ($_ (assertEqual (probe:coding-ok) True)))
     (println! INTEGRATED_CODING_TURN_OK))
