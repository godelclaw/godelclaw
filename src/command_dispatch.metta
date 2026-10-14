; The interpreter is shared; only its environment/string primitives differ.
!(import! &self ./src/engine_modes.py)
(= (dispatch:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (dispatch:cetta? (py-call (engine_modes.active_engine)))
     (progn (import! &self ./src/command_dispatch_cetta)
            (import! &self ./src/command_dispatch_hooks)
            (import! &self ./src/command_dispatch_core))
     (import_prolog_functions_from_file ./src/skills.pl
       (first_char run-command-batch-once command-batch-errors)))
