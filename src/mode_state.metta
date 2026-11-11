; One owner for public mode/lifecycle/description facades on both evaluators.
!(if (== (collapse (match &self (mode-state:loaded) True)) ())
   (progn
     (add-atom &self (mode-state:loaded))
     (progn
         (import! &self ./src/loop_modes_cetta.metta)
         (import! &self ./src/lifecycle_cetta.metta)
         (import! &self ./src/attention_cetta.metta)
         (add-atom &self (= (mode:process) (loop-mode:process-crossed)))
         (add-atom &self (= (mode:view) (loop-mode:view)))
         (add-atom &self (= (mode:list-view) (loop-mode:list-view)))
         (add-atom &self (= (mode:set $name) (loop-mode:set $name)))
         (add-atom &self (= (mode:wait-timed-out $value) (loop-mode:wait-timed-out $value)))
         (add-atom &self (= (lifecycle:enabled) (lifecycle-native:enabled)))
         (add-atom &self (= (lifecycle:view) (lifecycle-native:view)))
         (add-atom &self (= (lifecycle:start) (lifecycle-native:start)))
         (add-atom &self (= (lifecycle:stop) (lifecycle-native:stop)))
         (add-atom &self (= (attention:description) (attention-native:description))))) True)
