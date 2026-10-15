; SPDX-License-Identifier: MIT
; Optional NIK consumer. Ordinary API discovery has no module-runtime dependency.
!(import! &self (library lib_module))
!(import! &self ./src/api_registry.metta)

(= (api-module:list $rows)
   (if (== $rows ()) LNil
     (LCons (car-atom $rows) (api-module:list (cdr-atom $rows)))))
; The module's lexical String/Integer carriers are explicitly interpreted as
; native scalar values for this consumer. This is not a general interpretation
; proof or a migration certificate.
(= (api-module:scalar-types $types)
   (case $types
     ((LNil LNil)
      ((LCons (TypeDecl "String" CarrierTokenRaw) $tail)
       (LCons (TypeDecl "String" CarrierBuiltinString) (api-module:scalar-types $tail)))
      ((LCons (TypeDecl "Integer" CarrierTokenRaw) $tail)
       (LCons (TypeDecl "Integer" CarrierBuiltinInt) (api-module:scalar-types $tail)))
      ((LCons $type $tail) (LCons $type (api-module:scalar-types $tail))))))
(= (api-module:scalar-core (GSLTLanguageDefWireV1 $name $types $terms $equations $rewrites))
   (GSLTLanguageDefWireV1 $name (api-module:scalar-types $types) $terms $equations $rewrites))
(= (api-module:schema $path)
   (let* (($reader (langdef:load-baked-parser "module-core-reader"))
          ($document (module:read-file $reader "godelclaw:component-api" $path))
          ($_ (langdef:close-parser $reader))
          ($presentation (module:assemble $document "Schema" LNil LNil OriginViewV1 "godelclaw:component-api:schema"))
          ($core (module:core $presentation "ComponentAPI")))
     (api-module:scalar-core $core)))
(= (api-module:attach $schema $component)
   (let* (($base (GSLTLanguageDefWireV1 "ComponentMetadata" LNil LNil LNil LNil))
          ($fields (api-module:list (api:module-fields $component))))
     (module:attach-layer $base (SyntaxOriginV1 $component "public-api-v1") $schema "Field" $fields)))
