; CPython's reached POSIX path construction. All home expansion is delegated
; to the qualified native primitive, including eagerly evaluated defaults.
!(import! &self (library system))
!(import! &self (library str))
!(import! &self ./src/mode_io_cetta.metta)

(= (mode-path:expand $path)
   (let $value (catch (system:expanduser $path))
     (if (== (get-type $value) String) $value
       (case $value
         (((system:os-error $errno $message)
           (ErFailure (mio:errno-class $errno) $message $value))
          ($other (ErFailure NativeEvaluationError "native expanduser did not return text" $other)))))))
(= (mode-path:state-home)
   (let $fallback (mode-path:expand "~/.local/state")
     (if (mv:failed? $fallback) $fallback
       (mio:env "XDG_STATE_HOME" $fallback))))
(= (mode-path:engine)
   (let $configured (mio:env "METTACLAW_ENGINE_STATE_PATH" "")
     (if (!= $configured "") $configured
       (let $home (mode-path:state-home)
         (if (mv:failed? $home) $home
           (mio:join (mio:join $home (mio:env "METTACLAW_INSTANCE" "default")) "engine"))))))
(= (mode-path:failure)
   (let $configured (mio:env "METTACLAW_ENGINE_FAILURE_PATH" "")
     (if (!= $configured "") $configured
       (let $engine (mode-path:engine)
         (if (mv:failed? $engine) $engine
           (mio:join (mio:dirname $engine) "engine-failure"))))))
(= (mode-path:active-loop)
   (let $engine (mio:env "METTACLAW_ENGINE_STATE_PATH" "")
     (if (!= $engine "") (mio:join (mio:dirname $engine) "active-loop.json")
       (let $home (mode-path:state-home)
         (if (mv:failed? $home) $home
           (mio:join (mio:join $home (mio:env "METTACLAW_INSTANCE" "default")) "active-loop.json"))))))
; Pathlib removes empty and '.' components without resolving '..'. Preserve
; exactly two leading slashes, as both pathlib and posixpath.normpath do.
(= (mode-path:prefix $text)
   (if (and (str:starts-with $text "//") (not (str:starts-with $text "///"))) "//"
     (if (str:starts-with $text "/") "/" "")))
(= (mode-path:parts $text)
   (filter-atom (str:split "/" $text) $part (and (!= $part "") (!= $part "."))))
(= (mode-path:build $prefix $parts)
   (let $body (str:join "/" $parts)
     (if (== $body "") (if (== $prefix "") "." $prefix) (str:concat $prefix $body))))
(= (mode-path:pathlib $text) (mode-path:build (mode-path:prefix $text) (mode-path:parts $text)))
(= (mode-path:parent $text)
   (let $parts (mode-path:parts $text)
     (mode-path:build (mode-path:prefix $text) (erv:take $parts (- (size-atom $parts) 1)))))
(= (mode-path:lifecycle)
   (let $configured (mio:env "METTACLAW_LIFECYCLE_PATH" "")
     (if (!= $configured "") (mode-path:pathlib $configured)
       (let $home (mode-path:state-home)
         (if (mv:failed? $home) $home
           (mode-path:pathlib (mio:join
             (mio:join $home (mio:env "METTACLAW_INSTANCE" "pettaclaw-godel")) "lifecycle.json")))))))
(= (mode-path:commands $name)
   ; _engine_paths expands '~' even for an unknown engine or explicit roots.
   (let $home (mode-path:expand "~")
     (if (mv:failed? $home) $home
       (case $name
         (("petta" ((mio:join (mio:env "PETTA_ROOT" (mio:join $home "repos/PeTTa")) "run.sh")))
          ("cetta" ((mio:env "CETTA_BIN" (mio:join
            (mio:env "CETTA_ROOT" (mio:join $home "repos/CeTTa-runtime")) "cetta"))))
          ($other ()))))))
