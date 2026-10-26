; One evaluator bootstrap. Do not use active_engine to import active_engine.
; Insert public rules once: SWI-PeTTa can revisit transitive import sources.
!(import! &self ./src/mode_runtime.metta)
!(if (== (collapse (match &self (engine-modes:loaded) True)) ())
   (progn
     (add-atom &self (engine-modes:loaded))
     (if (mode-runtime:cetta?)
       (progn
         (import! &self ./src/engine_modes_cetta.metta)
         (add-atom &self (= (engine:active) (engine-native:active)))
         (add-atom &self (= (engine:selected) (engine-native:selected)))
         (add-atom &self (= (engine:view) (engine-native:view)))
         (add-atom &self (= (engine:set $name) (engine-native:set $name)))
         (add-atom &self (= (engine:command $name) (engine-native:command $name)))
         (add-atom &self (= (engine:recycle) (engine-native:recycle))))
       (progn
         (import! &self ./src/engine_modes_python.metta)
         (add-atom &self (= (engine:active) (engine-python:active)))
         (add-atom &self (= (engine:selected) (engine-python:selected)))
         (add-atom &self (= (engine:view) (engine-python:view)))
         (add-atom &self (= (engine:set $name) (engine-python:set $name)))
         (add-atom &self (= (engine:command $name) (engine-python:command $name)))
         (add-atom &self (= (engine:recycle) (engine-python:recycle)))))) True)
