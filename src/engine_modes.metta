; Insert the public rules once, however often the module is imported.
!(if (== (collapse (match &self (engine-modes:loaded) True)) ())
   (progn
     (add-atom &self (engine-modes:loaded))
     (progn
         (import! &self ./src/engine_modes_cetta.metta)
         (add-atom &self (= (engine:command $name) (engine-native:command $name)))
         (add-atom &self (= (engine:recycle) (engine-native:recycle))))) True)
