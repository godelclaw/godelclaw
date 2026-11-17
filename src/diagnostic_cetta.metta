; Diagnostic text is observed once, without changing the underlying result.
!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library system))
!(import! &self ./effect_receipt_values_cetta.metta)
(= (diagnostic:text $value $limit)
   (let* (($raw (if (== (get-type $value) String) $value (format-args "{}" (cons-atom $value ()))))
          ($masked (system:redact-secrets $raw))
          ($keys (re:replace-literal (re:compile "\\bsk-[A-Za-z0-9_-]{8,}\\b") $masked "[redacted]"))
          ($printable (str:from-python-codepoints (map-atom (tuple:from (str:python-codepoints $keys)) $cp
                        (if (erv:printable? $cp) $cp 32))))
          ($clean (str:trim (re:replace-literal (re:compile "\\s+") $printable " "))))
     (str:char-slice $clean 0 (min $limit (str:char-length $clean)))))
