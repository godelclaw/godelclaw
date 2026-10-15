; SPDX-License-Identifier: MIT
; A provider registers its descriptor as it loads. Get/set remain its own API.
!(import! &self ./src/settings_registry.metta)
!(import! &self ./src/ecan_parameters.metta)
!(import! &self ./src/attention_api.metta)
(= (am:bank-descriptors) (am:bank-descriptors (ecan:parameters)))
(= (am:bank-descriptors $specs)
   (if (== $specs ()) ()
     (let* (((ECANParameter $key $default $unit $domain $description) (car-atom $specs))
            ($name (repr $key))
            ($row (SettingSpec 1 "attention" $name $default $unit $domain "persistent" $description attention-policy attention-policy)))
       (cons-atom $row (am:bank-descriptors (cdr-atom $specs))))))
(SettingsProvider "attention" am:bank-descriptors)
