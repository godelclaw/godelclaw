; Keep the SWI backend for SWI-PeTTa; CeTTa utilities use native libraries.
!(import! &self ./src/engine_modes.metta)
(= (utils:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (utils:cetta? (engine:active))
     (import! &self ./src/utils_cetta)
     (import! &self ./src/utils_swi))
