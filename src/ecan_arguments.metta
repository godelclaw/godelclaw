; SPDX-License-Identifier: MIT
; Mathematical domains return ordinary error data, without changing a bank.
(= (ecan:finite-number $value)
   (if (== (get-type $value) Number)
     (if (isnan-math $value) False (not (isinf-math $value))) False))
(= (ecan:nonnegative $operation $field $value)
   (if (ecan:finite-number $value)
     (if (>= $value 0) True
       (ECANError $operation $field "expected a finite nonnegative number" $value))
     (ECANError $operation $field "expected a finite nonnegative number" $value)))
(= (ecan:fraction $operation $field $value)
   (if (ecan:finite-number $value)
     (if (and (>= $value 0) (<= $value 1)) True
       (ECANError $operation $field "expected a finite fraction in [0, 1]" $value))
     (ECANError $operation $field "expected a finite fraction in [0, 1]" $value)))
; Five named settings; there are no arbitrary upper bounds on half-lives.
(= (ecan:validate-setting $key $value)
   (case $key
     ((sti-half-life (ecan:nonnegative configure $key $value))
      (mti-half-life (ecan:nonnegative configure $key $value))
      (lti-half-life (ecan:nonnegative configure $key $value))
      (sti-mti-rate (ecan:fraction configure $key $value))
      (mti-lti-rate (ecan:fraction configure $key $value))
      ($other (ECANError configure $key "unknown setting" $value)))))
(= (ecan:validate-policy $settings)
   (if (== $settings ()) True
     (let (ECANSetting $key $value) (car-atom $settings)
       (let $check (ecan:validate-setting $key $value)
         (if (== $check True) (ecan:validate-policy (cdr-atom $settings)) $check)))))
(= (ecan:validate-retention $sti $mti $lti)
   (let $s (ecan:fraction decay sti-retain $sti)
     (if (== $s True)
       (let $m (ecan:fraction decay mti-retain $mti)
         (if (== $m True) (ecan:fraction decay lti-retain $lti) $m)) $s)))
(= (ecan:validate-consolidation $sti-mti $mti-lti)
   (let $s (ecan:fraction consolidate sti-mti-rate $sti-mti)
     (if (== $s True) (ecan:fraction consolidate mti-lti-rate $mti-lti) $s)))
