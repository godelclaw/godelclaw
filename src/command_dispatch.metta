; The interpreter is shared; only its environment/string primitives differ.
!(import! &self ./src/engine_modes.metta)
(= (dispatch:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (dispatch:cetta? (engine:active))
     (progn (import! &self ./src/command_dispatch_cetta)
            (import! &self ./src/command_dispatch_hooks)
            (import! &self ./src/command_dispatch_core))
     (import_prolog_functions_from_file ./src/skills.pl
       (first_char run-command-batch-once command-batch-errors)))
