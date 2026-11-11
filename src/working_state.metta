; Engine-selected working-state facade. Selection happens once at import so
; neither engine's working-state operations multiply later provider effects.
!(import! &self ./src/engine_modes.metta)
(= (working:native?)
   (case (engine:active) ((cetta True) ("cetta" True) ($other False))))
!(if (working:native?)
  (import! &self ./src/working_state_native.metta)
  (import! &self ./src/working_state_swi.metta))
