; Keep the SWI backend for SWI-PeTTa; CeTTa utilities use native libraries.
!(import! &self ./src/engine_modes.py)
(= (utils:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (utils:cetta? (py-call (engine_modes.active_engine)))
     (import! &self ./src/utils_cetta)
     (import! &self ./src/utils_swi))
