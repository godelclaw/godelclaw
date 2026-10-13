; Select process I/O once at startup. CeTTa's shell hot path is MeTTa/C.
!(import! &self ./src/engine_modes.py)
(= (shell-io:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (shell-io:cetta? (py-call (engine_modes.active_engine)))
     (import! &self ./src/shell_io_cetta)
     (import! &self ./src/shell_io_swi))
