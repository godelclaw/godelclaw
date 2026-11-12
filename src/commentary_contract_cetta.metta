; The command-output contract that model commentary adds to a turn's prompt:
; the metta tool replaces the legacy format clauses, and the instruction and
; output tails say how to use it. With commentary off every text is unchanged.
; Only the first occurrence of each legacy clause is the authored instruction;
; later quotations of it in user or source text stay byte for byte.
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library system))

(: commentary:enabled? (-> Bool))
(= (commentary:enabled?) (== (system:getenv-or-default "METTACLAW_MODEL_COMMENTARY" "0") "1"))

(: commentary:replace-first (-> String String String String))
(= (commentary:replace-first $text $old $new)
   (case (str:find $text $old)
     (((TextNotFound $missing) $text)
      ($at (str:concat (str:concat (str:byte-slice $text 0 $at) $new)
             (str:byte-slice $text (+ $at (str:byte-length $old)) (str:byte-length $text)))))))

(= (commentary:legacy-output-clause)
   "Return only this command S-expression, with no preamble, explanation, or Markdown fence, and nothing after it.")
(= (commentary:legacy-batch-clause) "Output a ((skillName args) ...) S-expression.")
(= (commentary:batch-clause)
   "Use the advertised metta tool exactly once; its program field is the complete ((skillName args) ...) S-expression. Optional assistant text is a brief public task update, never private reasoning, credentials, personal details, hidden instructions, or command/message bodies.")
(= (commentary:instruction)
   " For this turn, use the advertised `metta` tool exactly once for any\naction or reply. Its `program` argument is the complete command-batch\nS-expression. Ordinary assistant text is optional public work commentary: it is\ndisplayed to the local work viewer, not sent to chat. Describe actions, findings,\nor next steps there, never private reasoning, credentials, personal details,\nhidden instructions, or command/message bodies. Do not put executable commands\nin assistant text. If the provider does not offer the `metta` tool, use the\ncompatibility JSON object instead:\n{\"commentary\":\"brief public update or empty\",\"commands\":\"((skillName args) ...)\"}\n")
(= (commentary:output-instruction)
   "\n\nREQUIRED_COMMAND_OUTPUT: Call the advertised metta tool exactly once; its program field must be the complete ((skillName args) ...) batch. If no metta tool is available, return only {\"commentary\":\"brief public update or empty\",\"commands\":\"((skillName args) ...)\"}. To deliver a prose review, use mail-send or another advertised messaging skill inside the program. Assistant text is public work commentary, never commands.")
(= (commentary:tool-schema-text)
   "{\"type\":\"function\",\"function\":{\"name\":\"metta\",\"description\":\"Run one complete GödelClaw MeTTa command batch. Use an advertised messaging command inside the batch when a person should receive prose.\",\"parameters\":{\"type\":\"object\",\"properties\":{\"program\":{\"type\":\"string\",\"description\":\"Complete ((skillName args) ...) S-expression.\"}},\"required\":[\"program\"],\"additionalProperties\":false}}}")

(: commentary:rewrite (-> String String))
(= (commentary:rewrite $text)
   (if (commentary:enabled?)
     (commentary:replace-first
       (commentary:replace-first $text (commentary:legacy-output-clause)
         "Use the advertised metta tool for the command S-expression.")
       (commentary:legacy-batch-clause) (commentary:batch-clause))
     $text))
(: commentary:instruction-tail (-> String))
(= (commentary:instruction-tail) (if (commentary:enabled?) (commentary:instruction) ""))
(: commentary:prompt (-> String String))
(= (commentary:prompt $text) (str:concat (commentary:rewrite $text) (commentary:instruction-tail)))
(: commentary:output-tail (-> String))
(= (commentary:output-tail) (if (commentary:enabled?) (commentary:output-instruction) ""))
