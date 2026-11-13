; One background refresh of a provider observation, started by the model
; controls (src/model_menu_cetta.metta), which hand it the credential alias:
;   cetta --lang petta --import-mode ancestor-walk --quiet src/provider_observation_main.metta KIND
!(import! &self ./model_menu_cetta.metta)
!(model:observe (format-args "{}" ((car-atom (system:args)))))
