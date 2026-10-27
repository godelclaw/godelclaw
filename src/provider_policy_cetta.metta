; Storage-independent provider configuration. No provider or persistence effects.
; Resolve consumes decoded JSON and explicit environment observations as data.
!(import! &self (library system))
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library math))

(= (pp:object? $value)
  (case $value (((JsonObject $pairs) True) ($other False))))

; Last occurrence wins, as in the existing configuration reader. Wrap present
; values so null, empty text and invalid values cannot become absence.
(= (pp:field $object $key $boundary)
  (let (JsonObject $pairs) $object
    (pp:lookup $pairs $key $boundary ProviderPolicyAbsentV2)))
(= (pp:lookup $pairs $key $boundary $found)
  (if (== $pairs ()) $found
    (let (JsonPair $name $value) (car-atom $pairs)
      (pp:lookup (cdr-atom $pairs) $key $boundary
        (if (== $name $key) (PPSelectedV2 $value $boundary json) $found)))))
(= (pp:choose $first $second)
  (case $first ((ProviderPolicyAbsentV2 $second) ($present $present))))
(= (pp:env-field $value $boundary)
  (case $value
    ((ProviderPolicyAbsentV2 ProviderPolicyAbsentV2)
     ((ProviderPolicyValueV2 $text) (PPSelectedV2 $text $boundary environment))
     ($other (PPSelectedV2 $other $boundary invalid)))))
(= (pp:default-profile $mode)
  (if (or (== $mode "iter") (== $mode "omega")) "upstream" "managed"))
(= (pp:map $field $boundary)
  (case $field
    ((ProviderPolicyAbsentV2 (JsonObject ()))
     ((PPSelectedV2 $value $where $kind)
      (if (pp:object? $value) $value
        (ProviderPolicyRefusedV2 invalid-object $boundary))))))

(= (provider-policy:resolve $mode $config $env-profile $env-delay)
  (if (== (get-type $mode) String)
    (pp:resolve-mode $mode $config $env-profile $env-delay)
    (ProviderPolicyRefusedV2 invalid-mode "mode")))
(= (pp:resolve-mode $mode $config $env-profile $env-delay)
  (if (pp:object? $config)
    (case (pp:map (pp:field $config "modes" "modes") "modes")
      (((ProviderPolicyRefusedV2 $reason $detail) (ProviderPolicyRefusedV2 $reason $detail))
       ($modes
        (case (pp:map (pp:field $modes $mode (ProviderPolicyFieldV2 "modes" $mode))
                      (ProviderPolicyFieldV2 "modes" $mode))
          (((ProviderPolicyRefusedV2 $reason $detail) (ProviderPolicyRefusedV2 $reason $detail))
           ($override (pp:resolve-fields $mode $config $override $env-profile $env-delay)))))))
    (ProviderPolicyRefusedV2 invalid-object "config")))

(= (pp:resolve-fields $mode $config $override $env-profile $env-delay)
  (let* (($profile (pp:choose (pp:field $override "profile" (ProviderPolicyFieldV2 "modes" $mode "profile"))
                    (pp:choose (pp:env-field $env-profile "METTACLAW_LLM_POLICY")
                      (pp:choose (pp:field $config "profile" "profile")
                        (PPSelectedV2 (pp:default-profile $mode) "default.profile" json)))))
         ($delay (pp:choose (pp:env-field $env-delay "METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS")
                   (pp:choose (pp:field $override "quota_cooldown_seconds"
                                (ProviderPolicyFieldV2 "modes" $mode "quota_cooldown_seconds"))
                     (pp:choose (pp:field $config "quota_cooldown_seconds" "quota_cooldown_seconds")
                       (PPSelectedV2 300 "default.quota_cooldown_seconds" json))))))
    (case (pp:profile $profile)
      (((ProviderPolicyRefusedV2 $reason $detail) (ProviderPolicyRefusedV2 $reason $detail))
       ($tag (pp:cooldown $tag $delay))))))

(= (pp:profile (PPSelectedV2 $value $boundary $kind))
  (if (== (get-type $value) String)
    (case $value
      (("managed" ProviderManagedV2) ("upstream" ProviderUpstreamV2)
       ($other (ProviderPolicyRefusedV2 invalid-profile $boundary))))
    (ProviderPolicyRefusedV2 invalid-profile $boundary)))
(= (pp:integer? $value)
  (if (== (get-metatype $value) Expression) False
    (case (math:integer-divmod $value 1)
      (((IntegerDivmodV1 $integer 0) True) ($other False)))))
(= (pp:cooldown $profile (PPSelectedV2 $value $boundary $kind))
  (case $kind
    ((environment
      (if (== (get-type $value) String)
        (let* (($text (str:trim $value))
               ($digits (if (or (str:starts-with $text "+") (str:starts-with $text "-"))
                          (str:char-slice $text 1 (str:char-length $text)) $text)))
          (if (and (not (== $digits "")) (str:is-all-in digit $digits))
            (pp:duration $profile (str:string->number $text) $boundary)
            (ProviderPolicyRefusedV2 invalid-cooldown $boundary)))
        (ProviderPolicyRefusedV2 invalid-cooldown $boundary)))
     (json (pp:duration $profile $value $boundary))
     ($other (ProviderPolicyRefusedV2 invalid-cooldown $boundary)))))
(= (pp:duration $profile $seconds $boundary)
  (if (pp:integer? $seconds)
    (if (< $seconds 0) (ProviderPolicyRefusedV2 negative-cooldown $boundary)
      (ProviderPolicyV2 $profile (* $seconds 1000000000)))
    (ProviderPolicyRefusedV2 invalid-cooldown $boundary)))

; Acquisition is confined to load. Arbitrary default atoms preserve unset
; versus empty without reserving any possible environment string.
(= (pp:env $name)
  (case (system:getenv-or-default $name ProviderPolicyAbsentV2)
    ((ProviderPolicyAbsentV2 ProviderPolicyAbsentV2)
     ($text (ProviderPolicyValueV2 $text)))))
(= (provider-policy:load $mode)
  (if (== (get-type $mode) String) (pp:load-mode $mode)
    (ProviderPolicyRefusedV2 invalid-mode "mode")))
(= (pp:load-mode $mode)
  (let* (($path (system:getenv-or-default "METTACLAW_LLM_POLICY_PATH" ProviderPolicyAbsentV2))
         ($profile (pp:env "METTACLAW_LLM_POLICY"))
         ($delay (pp:env "METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS")))
    (case $path
      ((ProviderPolicyAbsentV2 (provider-policy:resolve $mode (JsonObject ()) $profile $delay))
       ("" (provider-policy:resolve $mode (JsonObject ()) $profile $delay))
       ($supplied (pp:load-file $mode $supplied $profile $delay))))))
(= (pp:load-file $mode $path $profile $delay)
  ; Reject non-files before reading (in particular, never block on a FIFO).
  (case (fs:metadata $path follow)
    (((fs:info regular $size $identity)
      (case (fs:read-text-result $path 0 "raw")
        ((("ok" $text)
          (case (json:parse $text)
            (((JsonObject $pairs) (provider-policy:resolve $mode (JsonObject $pairs) $profile $delay))
             ((JsonFailureV1 $operation $reason $call $detail)
              (ProviderPolicyRefusedV2 invalid-json (ProviderPolicyBoundaryV2 "json:parse" $path $reason $detail)))
             ($other (ProviderPolicyRefusedV2 invalid-config
                       (ProviderPolicyBoundaryV2 "json:parse object" $path $other))))))
         ($error (ProviderPolicyRefusedV2 file-read
                   (ProviderPolicyBoundaryV2 "fs:read-text-result" $path $error))))))
     ($error (ProviderPolicyRefusedV2 file-observation
               (ProviderPolicyBoundaryV2 "fs:metadata regular file" $path $error))))))
