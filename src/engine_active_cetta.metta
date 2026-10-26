; Environment only. No mode files, home lookup, Python, or cached selection.
!(import! &self (library system))
!(import! &self (library str))
!(import! &self ./src/mode_text_cetta.metta)

(= (engine-native:active)
   (engine-native:environment-name (system:getenv-or-default "METTACLAW_ACTIVE_ENGINE"
     (system:getenv-or-default "METTACLAW_ENGINE" "petta"))))
(= (engine-native:environment-name $raw)
   (if (== $raw "cetta") "cetta"
       (if (== $raw "petta") "petta"
         (case (str:utf8-error $raw)
           ((StrUtf8WellFormedV1
             (if (== (mode-text:name $raw) "cetta") "cetta" "petta"))
            ; os.environ decodes invalid OS bytes using surrogateescape. None
            ; of those surrogate characters can form a recognized ASCII name.
            ((StrUtf8ErrorV1 $_ $_ $_ $_) "petta")
            ($other (Error engine-native:active $other)))))))
