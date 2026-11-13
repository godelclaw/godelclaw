; The model controls of the operator's menus and the agent's skills, read from
; local durable state only: the provider catalogs and quota last observed (the
; provider observations file), and the selected model (src/model_selection_cetta.metta).
; A control never waits on a provider. A stale observation is refreshed by a
; background child (src/provider_observation_main.metta), started at most once
; in its retry interval, whose own request runs to its deadline.
;
;   (model:menu-state)  (ModelMenuV1 ids current unavailable status)
;   (model:set NAME)    the reply, after validation and persistence
;   (model:quota)       the quota line
;   (model:models)      the catalog as a list of (id ctx N provider P) items
;   (model:session-problem NAME)  why NAME cannot serve a coding session, or ""
!(import! &self (library fs))
!(import! &self (library io))
!(import! &self (library json))
!(import! &self (library math))
!(import! &self (library path))
!(import! &self (library proc))
!(import! &self (library re))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./model_selection_cetta.metta)
!(import! &self ./state_file_cetta.metta)

;; ------------------------------------------------------------ lists
; Catalogs can be long: lists are built by the engine's own concatenation,
; collapse and filter, never by consing one item at a time (each cons-atom
; copies the list it extends).

(= (model:append $front $back) (union-atom $front $back))
(= (model:member? $item $items)
   (if (== $items ()) False (if (== (car-atom $items) $item) True (model:member? $item (cdr-atom $items)))))
; ITEMS, then each of EXTRA not already there, in order (unique-atom keeps
; first occurrences, in linear time).
(= (model:add-new $items $extra) (unique-atom (model:append $items $extra)))

;; ------------------------------------------------------------ values

(= (model:env-set? $name) (not (== (system:getenv-or-default $name "") "")))
; Python's truth of a JSON value.
(= (model:truthy? $value)
   (case $value
     ((JsonNull False) ("" False) (0 False) (0.0 False) ((JsonArray ()) False) ((JsonObject ()) False)
      ($other (if (== (get-type $other) Bool) $other True)))))
; Python's str() of a JSON value.
(= (model:python-str $value)
   (case $value
     ((JsonNull "None")
      ($other (if (== (get-type $other) String) $other
                (if (== (get-type $other) Bool) (if $other "True" "False") (json:stringify-python $other)))))))
(= (model:object $value) (case $value (((JsonObject $pairs) (JsonObject $pairs)) ($other (JsonObject ())))))
(= (model:number $value $default) (if (== (get-type $value) Number) $value $default))
; KEY's value, or "?" when the object has no such key.
(= (model:shown $object $key)
   (case (json:get-first $object $key) (((JsonFoundV1 $index $value) (model:python-str $value)) ($missing "?"))))

;; ------------------------------------------------------------ models

(= (model:anthropic? $name) (str:starts-with (str:strip-python $name) "claude-"))
(= (model:anthropic-default)
   "claude-fable-5,claude-opus-5-5,claude-opus-5,claude-opus-4-8,claude-sonnet-5-5,claude-sonnet-5,claude-haiku-4-5-20251001")
(= (model:anthropic-models)
   (model:nonblank (str:split "," (system:getenv-or-default "ANTHROPIC_MODELS" (model:anthropic-default)))))
(= (model:nonblank $items)
   (if (== $items ()) ()
     (let* (($item (str:strip-python (car-atom $items))) ($rest (model:nonblank (cdr-atom $items))))
       (if (== $item "") $rest (cons-atom $item $rest)))))
; Subscription choices observed on 2026-10-15: pinned, not a claim of the
; account's current availability.
(= (model:chatgpt-choices) ("chatgpt:gpt-5.6-sol" "chatgpt:gpt-6-sol" "chatgpt:gpt-6.1-sol" "chatgpt:gpt-6-astra"))
(= (model:chatgpt-policy-path)
   (let $configured (system:getenv-or-default "METTACLAW_CHATGPT_POLICY_PATH" "")
     (if (== $configured "")
       (path:join (path:dirname (system:getenv-or-default "METTACLAW_ENGINE_STATE_PATH"
                                  (system:expanduser "~/.local/state/godelclaw/engine"))) "chatgpt.json")
       $configured)))
; The menu offers the pinned subscription choices on the native transport
; once an account is selected.
(= (model:chatgpt-menu?)
   (if (== (system:getenv-or-default "METTACLAW_LLM_TRANSPORT" "") "native")
     (case (fs:read-text-result (model:chatgpt-policy-path) 0 "raw")
       ((("ok" $text) (model:truthy? (json:get (model:object (json:parse-python $text)) "account")))
        ($unreadable False)))
     False))
!(bind! &model-id (new-state (re:compile "[A-Za-z0-9][A-Za-z0-9._:/+@-]*")))
(= (model:valid-id? $name)
   (if (> (str:byte-length $name) 200) False
     (case (re:full-match (get-state &model-id) $name) ((re:no-match False) ($match True)))))
; Whether NAME can be selected: ModelUsable, or (ModelUnusable text unchanged),
; where unchanged says that a switch adds "; model unchanged".
(= (model:problem $name)
   (if (model:anthropic? $name)
     (if (not (model:env-set? "ANTHROPIC_API_KEY"))
       (ModelUnusable "anthropic provider not configured (ANTHROPIC_API_KEY missing)" True)
       (let $known (model:anthropic-models)
         (if (model:member? $name $known) ModelUsable
           (ModelUnusable (str:join "" ("unknown anthropic model '" $name "'; available: " (str:join ", " $known))) False))))
     (if (model:valid-id? $name) ModelUsable
       (ModelUnusable (str:join "" ("invalid model id '" $name "'")) True))))
(= (model:session-problem $requested)
   (case (model:problem (str:strip-python (format-args "{}" ($requested))))
     ((ModelUsable "") ((ModelUnusable $text $unchanged) $text))))

;; ------------------------------------------------------------ observations

(= (model:observation-path)
   (let $configured (system:getenv-or-default "METTACLAW_PROVIDER_OBSERVATION_PATH" "")
     (if (== $configured "")
       (path:join (system:getenv-or-default "XDG_STATE_HOME" (system:expanduser "~/.local/state"))
                  (path:join "pettaclaw" (path:join (system:getenv-or-default "METTACLAW_INSTANCE" "default")
                                                    "provider-observations.json")))
       $configured)))
; The observations object, or an empty one for a missing, unreadable or
; malformed file, or one of another schema.
(= (model:observations)
   (case (fs:read-text-result (model:observation-path) 0 "raw")
     ((("ok" $text)
       (let $value (model:object (json:parse-python $text))
         (if (== (json:get $value "schema") 1) $value (JsonObject ()))))
      ($unreadable (JsonObject ())))))
(= (model:record $observations $kind) (model:object (json:get $observations $kind)))
(= (model:stamp $record $key) (model:number (json:get $record $key) 0))
(= (model:rows $observations $kind)
   (case (json:get (model:record $observations $kind) "value") (((JsonArray $rows) $rows) ($other ()))))
; The ids a catalog lists, each once, in order.
(= (model:catalog-ids $observations $kind) (unique-atom (model:row-ids (model:rows $observations $kind))))
(= (model:row-ids $rows) (collapse (let $row (superpose $rows) (model:row-id $row))))
(= (model:row-id $row)
   (case $row
     (((JsonObject $pairs) (let $id (json:get $row "id") (if (model:truthy? $id) (model:python-str $id) (empty))))
      ($other (empty)))))

;; ------------------------------------------------------------ the menu

; The switchable ids: the catalogs observed, the pinned subscription choices,
; the selected model when no catalog lists it, then the configured Anthropic
; models. So the menu never offers a model a catalog does not serve.
(= (model:ids $observations $selected)
   (let* (($catalogs (model:add-new (model:catalog-ids $observations "models") (model:catalog-ids $observations "kimi-models")))
          ($pinned (if (model:chatgpt-menu?) (model:add-new $catalogs (model:chatgpt-choices)) $catalogs))
          ($chosen (if (model:anthropic? $selected) $pinned (model:add-new $pinned ($selected)))))
     (if (model:env-set? "ANTHROPIC_API_KEY") (model:add-new $chosen (model:anthropic-models)) $chosen)))
; Each catalog's ids as a set, read once for a menu: (ModelCatalogsV1
; synthetic kimi anthropic), a catalog None when it lists nothing, anthropic
; the configured models, or () without a credential.
(= (model:catalogs $observations)
   (ModelCatalogsV1 (model:id-set (model:catalog-ids $observations "models"))
                    (model:id-set (model:catalog-ids $observations "kimi-models"))
                    (if (model:env-set? "ANTHROPIC_API_KEY") (model:anthropic-models) ())))
(= (model:id-set $ids) (if (== $ids ()) None (str:set $ids)))
; True, False, or None when no observation can establish it: a catalog
; observes only its own provider's models.
(= (model:available (ModelCatalogsV1 $synthetic $kimi $anthropic) $name)
   (if (or (str:starts-with $name "chatgpt:") (str:starts-with $name "openai:")) None
     (if (model:anthropic? $name) (model:member? $name $anthropic)
       (let $set (if (str:starts-with $name "kimi:") $kimi $synthetic)
         (if (== $set None) None (str:is-set-member $set $name))))))
(= (model:in-catalog $observations $name) (model:available (model:catalogs $observations) $name))
(= (model:unavailable $observations $ids) (model:unavailable-in (model:catalogs $observations) $ids))
(= (model:unavailable-in $catalogs $ids) (filter-atom $ids $id (== (model:available $catalogs $id) False)))
(= (model:catalog-note $observations $kind $provider $now)
   (let $observed (model:stamp (model:record $observations $kind) "observed_at")
     (if (== $observed 0) (str:concat $provider " catalog refreshing")
       (str:join "" ($provider " catalog observed " (repr (max 0 (math:floor (- $now $observed)))) "s ago")))))
; Where the choices come from, and how old each catalog is.
(= (model:catalog-status $observations)
   (let* (($now (system:wall-time))
          ($kimi (if (and (== (model:record $observations "kimi-models") (JsonObject ()))
                          (not (model:env-set? "KIMI_API_KEY"))) ()
                   ((model:catalog-note $observations "kimi-models" "Kimi Code" $now))))
          ($pinned (if (model:chatgpt-menu?) ("pinned ChatGPT subscription choices (availability unverified)") ())))
     (str:join "; " (cons-atom (model:catalog-note $observations "models" "Synthetic" $now) (model:append $kimi $pinned)))))
(= (model:menu-state)
   (let* (($observations (model:observations))
          ($current (model:current syn:large:text))
          ($ids (model:ids $observations $current))
          ($state (ModelMenuV1 $ids $current (model:unavailable $observations $ids) (model:catalog-status $observations)))
          ($refreshed (model:refresh ("models"))))
     $state))
; The catalogs as the agent's skill lists them.
(= (model:models)
   (let* (($refreshed (model:refresh ("models")))
          ($observations (model:observations))
          ($items (model:append (model:catalog-items (model:rows $observations "models") "synthetic")
                    (model:append (model:catalog-items (model:rows $observations "kimi-models") "kimi")
                      (if (model:env-set? "ANTHROPIC_API_KEY") (model:anthropic-items (model:anthropic-models)) ())))))
     (if (== $items ()) "(catalog refreshing)" (str:join "" ("(" (str:join " " $items) ")")))))
(= (model:catalog-items $rows $provider) (collapse (let $row (superpose $rows) (model:catalog-item $row $provider))))
(= (model:catalog-item $row $provider)
   (case $row
     (((JsonObject $pairs)
       (let $context (json:get $row "context")
         (str:join "" ("(" (model:shown $row "id") " ctx " (if (model:truthy? $context) (model:python-str $context) "?")
                       " provider " $provider ")"))))
      ($other (empty)))))
(= (model:anthropic-items $models) (map-atom $models $model (str:join "" ("(" $model " provider anthropic)"))))

;; ------------------------------------------------------------ the switch

; The choice as the state file's one (active-model NAME) line, after the
; file's other lines, written to a sibling, synced and renamed into place.
; A new file takes the process's default mode. True when it landed.
!(bind! &model-choice-line (new-state (re:compile "\\s*\\(active-model\\s")))
(= (model:persist $name)
   (let* (($path (model:state-path))
          ($kept (case (fs:read-text-result $path 0 "raw") ((("ok" $text) (model:other-lines (model:lines $text))) ($missing ()))))
          ($written (str:concat (str:join "\n" (model:append $kept ((str:join "" ("(active-model " $name ")"))))) "\n")))
     (model:replace $path $written)))
; Python's lines of a text written with \n or \r\n endings.
(= (model:lines $text)
   (if (== $text "") ()
     (let $pieces (str:split "\n" $text)
       (model:unterminated (if (str:ends-with $text "\n") (model:without-last $pieces) $pieces)))))
(= (model:without-last $items)
   (if (== (cdr-atom $items) ()) () (cons-atom (car-atom $items) (model:without-last (cdr-atom $items)))))
(= (model:unterminated $lines)
   (if (== $lines ()) ()
     (let $line (car-atom $lines)
       (cons-atom (if (str:ends-with $line "\r") (str:byte-slice $line 0 (- (str:byte-length $line) 1)) $line)
                  (model:unterminated (cdr-atom $lines))))))
(= (model:other-lines $lines)
   (if (== $lines ()) ()
     (let $rest (model:other-lines (cdr-atom $lines))
       (case (re:find (get-state &model-choice-line) (car-atom $lines))
         (((re:match 0 $end $groups) $rest) ($other (cons-atom (car-atom $lines) $rest)))))))
(= (model:replace $path $text)
   (let* (($temporary (str:concat $path ".tmp"))
          ($writer (fs:open-write $temporary ("create" "truncate") 438)))
     (if (sf:failed? $writer) False
       (let* (($wrote (fs:write-all $writer $text))
              ($synced (if (sf:failed? $wrote) $wrote (fs:sync $writer)))
              ($closed (fs:close $writer)))
         (if (sf:failed? $synced) (let $removed (fs:remove $temporary) False)
           (not (sf:failed? (fs:rename $temporary $path))))))))
(= (model:provider-label $name)
   (if (str:starts-with $name "kimi:") "Kimi Code API"
     (if (str:starts-with $name "openai:") "OpenAI API"
       (if (str:starts-with $name "chatgpt:") "ChatGPT subscription" "Synthetic"))))
; Switch the model of later provider calls: validated, then durably persisted.
; A switch that cannot be persisted is not applied; one that is survives
; restarts, since every process reads the state file.
(= (model:set $requested)
   (let $name (str:strip-python (format-args "{}" ($requested)))
     (case (model:problem $name)
       (((ModelUnusable $text True) (str:concat $text "; model unchanged"))
        ((ModelUnusable $text False) $text)
        (ModelUsable
         (if (not (model:persist $name))
           (str:join "" ("switch to '" $name "' NOT applied: could not durably persist the choice"))
           (if (model:anthropic? $name) (str:join "" ("model set to '" $name "' (anthropic); persists across restarts"))
             (let* (($refreshed (model:refresh ("models")))
                    ($absent (if (== (model:in-catalog (model:observations) $name) False)
                               "; warning: absent from the latest provider catalog, so the provider may reject it" ""))
                    ($kimi (if (and (str:starts-with $name "kimi:") (not (model:env-set? "KIMI_API_KEY")))
                             "; Kimi Code credential is not configured" "")))
               (str:join "" ("model set to '" $name "' (" (model:provider-label $name) "); persists across restarts"
                             $absent $kimi))))))))))

;; ------------------------------------------------------------ quota

(= (model:usage-path) (system:getenv-or-default "METTACLAW_ANTHROPIC_USAGE_PATH" "memory/anthropic_usage.json"))
(= (model:tokens $value)
   (let $n (math:floor (model:number $value 0))
     (if (>= $n 1000000) (str:concat (str:format-fixed (/ $n 1000000.0) 1) "M")
       (if (>= $n 1000) (str:concat (repr (math:floor (/ $n 1000))) "k") (repr $n)))))
; Today's Anthropic tokens, as the usage tally (src/completion_effects_cetta.metta)
; counts them; nothing when there are none.
(= (model:anthropic-today)
   (case (fs:read-text-result (model:usage-path) 0 "raw")
     ((("ok" $text)
       (let* (($usage (model:object (json:parse-python $text))) ($in (json:get $usage "in")) ($out (json:get $usage "out")))
         (if (or (model:truthy? $in) (model:truthy? $out))
           ((str:join "" ("today=" (model:tokens $in) " in / " (model:tokens $out) " out"))) ())))
      ($unreadable ()))))
; float(value) rounded to one decimal, or "?".
(= (model:percent $value)
   (if (== (get-type $value) Number) (str:format-fixed $value 1)
     (if (== (get-type $value) Bool) (if $value "1.0" "0.0")
       (if (== (get-type $value) String)
         (case (catch (str:string->number (str:strip-python $value)))
           (((Error $call $why) "?") ($number (if (== (get-type $number) Number) (str:format-fixed $number 1) "?"))))
         "?"))))
(= (model:limits $data)
   (let* (($plan (model:object (json:get $data "planWeeklyLimit")))
          ($week (model:object (json:get $data "weeklyTokenLimit")))
          ($five (model:object (json:get $data "rollingFiveHourLimit")))
          ($planned (if (== $plan (JsonObject ())) ()
                      ((str:join "" ("synthetic_weekly=" (model:percent (json:get $plan "percentRemaining")) "% remaining")))))
          ($weekly (if (or (== $week (JsonObject ())) (not (== $plan (JsonObject ())))) ()
                     ((str:join "" ("synthetic_weekly=" (model:shown $week "remainingCredits") " of " (model:shown $week "maxCredits")
                                    " (" (model:percent (json:get $week "percentRemaining")) "%)")))))
          ($window (if (== $five (JsonObject ())) ()
                     ((str:join "" ("5h=" (model:shown $five "remaining") "/" (model:shown $five "max")
                                    (if (model:truthy? (json:get $five "limited")) " LIMITED" "")))))))
     (model:append $planned (model:append $weekly $window))))
; The selected model and its provider, today's Anthropic tokens on an
; Anthropic model, and the Synthetic budget last observed.
(= (model:quota)
   (let* (($selected (model:current syn:large:text))
          ($anthropic (model:anthropic? $selected))
          ($tag (if $anthropic "Anthropic API"
                  (if (str:starts-with $selected "chatgpt:") "ChatGPT subscription"
                    (if (str:starts-with $selected "kimi:") "Kimi Code API"
                      (if (str:starts-with $selected "openai:") "OpenAI API" "Synthetic")))))
          ($today (if $anthropic (model:anthropic-today) ()))
          ($data (model:object (json:get (model:record (model:observations) "quota") "value")))
          ($refreshed (model:refresh ("quota")))
          ($limits (if (== $data (JsonObject ()))
                     (if (== $today ()) ("quota refreshing; no cached observation") ())
                     (model:limits $data))))
     (str:join " | " (cons-atom (str:join "" ("model=" $selected " (" $tag ")")) (model:append $today $limits)))))

;; ------------------------------------------------------------ refresh

; An observation's time to live, in seconds, and the least interval between
; two attempts at it.
(= (model:ttl "models") 300.0)
(= (model:ttl "kimi-models") 300.0)
(= (model:ttl "quota") 60.0)
(= (model:retry-seconds) 30.0)
(= (model:refresh-due? $record $kind $now)
   (let* (($observed (model:stamp $record "observed_at")) ($attempted (model:stamp $record "attempted_at")))
     (and (or (== $observed 0) (>= (- $now $observed) (model:ttl $kind)))
          (or (== $attempted 0) (>= (- $now $attempted) (model:retry-seconds))))))
; Refresh each of KINDS ("models", "quota") that is due; "models" includes
; Kimi Code's catalog when its credential is configured. The kinds started.
(= (model:refresh $kinds)
   (let $expanded (if (and (model:member? "models" $kinds) (model:env-set? "KIMI_API_KEY"))
                    (model:add-new $kinds ("kimi-models")) $kinds)
     (model:refresh-each $expanded (system:wall-time))))
(= (model:refresh-each $kinds $now)
   (if (== $kinds ()) ()
     (let* (($kind (car-atom $kinds))
            ($rest (model:refresh-each (cdr-atom $kinds) $now)))
       (if (model:refresh-due? (model:record (model:observations) $kind) $kind $now)
         (if (model:start-refresh $kind $now) (cons-atom $kind $rest) $rest)
         $rest))))
; The attempt is stamped first, so a second control within the retry interval
; starts none; the child then runs on its own, its output discarded.
(= (model:start-refresh $kind $now)
   (if (not (model:update $kind (("attempted_at" $now)) False)) False
     (let* (($engine (system:executable))
            ($program (path:join (system:cwd) "src/provider_observation_main.metta"))
            ($credential (model:credential-env $kind))
            ($child (catch (proc:spawn ($engine "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet" $program $kind)
                                       ((env (inherit $credential)) (lifetime detached) (stdout null) (stderr null))))))
       (case $child
         (((Error $call $why) False) ((proc:failed $why) False)
          ($owner (let $closed (proc:close $owner) True)))))))
; Each provider's credential is an alias the child's requests name; the key
; stays in the environment.
(= (model:credential-alias "kimi-models") "OBSERVE_KIMI")
(= (model:credential-alias "models") "OBSERVE_SYNTHETIC")
(= (model:credential-alias "quota") "OBSERVE_SYNTHETIC")
(= (model:credential-env $kind)
   (let* (($alias (str:concat "CETTA_IO_AUTH_" (model:credential-alias $kind)))
          ($kimi (== $kind "kimi-models")))
     (((str:concat $alias "_URL_PREFIX") (str:concat (if $kimi (model:kimi-base) (model:api-root)) "/"))
      ((str:concat $alias "_HEADER") "Authorization")
      ((str:concat $alias "_ENV") (if $kimi "KIMI_API_KEY" "SYNTHETIC_API_KEY"))
      ((str:concat $alias "_PREFIX") "Bearer "))))
(= (model:rstrip-slash $text)
   (if (str:ends-with $text "/") (model:rstrip-slash (str:byte-slice $text 0 (- (str:byte-length $text) 1))) $text))
(= (model:synthetic-base)
   (model:rstrip-slash (system:getenv-or-default "SYNTHETIC_BASE_URL" "https://api.synthetic.new/openai/v1")))
; The provider's root: its base before "/openai/".
(= (model:api-root)
   (let $base (system:getenv-or-default "SYNTHETIC_BASE_URL" "https://api.synthetic.new/openai/v1")
     (model:rstrip-slash (car-atom (str:split "/openai/" $base)))))
(= (model:kimi-base) (model:rstrip-slash (system:getenv-or-default "KIMI_BASE_URL" "https://api.kimi.ai/coding/v1")))
; Merge FIELDS ((key value) ...) into KIND's record, or with REPLACE make
; them the whole record, and write the file back as Python's json.dump wrote
; it, readable by its owner only. One step under the file's lock, so two
; refreshes that finish together both land. True when it landed.
(= (model:update $kind $fields $replace)
   (let* (($path (model:observation-path))
          ($made (fs:make-directories (path:dirname $path) 511)))
     (case (sf:locked (str:concat $path ".lock") 384
             (let* (($observations (model:observations))
                    ($record (model:put-all (if $replace (JsonObject ()) (model:record $observations $kind)) $fields)))
               (model:write-observations $path (model:put (model:put $observations "schema" 1) $kind $record))))
       ((True True) ($failed False)))))
(= (model:put (JsonObject $pairs) $key $value)
   (JsonObject (model:put-pair $pairs $key $value)))
(= (model:put-pair $pairs $key $value)
   (if (== $pairs ()) ((JsonPair $key $value))
     (case (car-atom $pairs)
       (((JsonPair $key $old) (cons-atom (JsonPair $key $value) (cdr-atom $pairs)))
        ($other (cons-atom $other (model:put-pair (cdr-atom $pairs) $key $value)))))))
(= (model:put-all $object $fields)
   (if (== $fields ()) $object
     (let ($key $value) (car-atom $fields) (model:put-all (model:put $object $key $value) (cdr-atom $fields)))))
(= (model:write-observations $path $observations)
   (let $text (str:concat (json:stringify-python-with $observations ((ensure-ascii True) (sort-keys True))) "\n")
     (case (sf:replace $path $text 384 True) ((True True) ((SfUncertain $unsynced) True) ($failed False)))))

;; ------------------------------------------------------------ the refresh child

(= (model:introspect-ms)
   (case (catch (str:string->number (system:getenv-or-default "SYNTHETIC_INTROSPECT_TIMEOUT" "8")))
     (((Error $call $why) 8000) ($seconds (if (> $seconds 0) (math:floor (* 1000 $seconds)) 8000)))))
; KIND's provider request: its URL, its headers and the variable holding its key.
(= (model:request "models")
   ((str:concat (model:synthetic-base) "/models") ((http:credential "OBSERVE_SYNTHETIC")) "SYNTHETIC_API_KEY"))
(= (model:request "kimi-models")
   ((str:concat (model:kimi-base) "/models")
    ((http:credential "OBSERVE_KIMI") (http:header "User-Agent" "godelclaw/1.0") (http:header "Accept" "application/json"))
    "KIMI_API_KEY"))
(= (model:request "quota")
   ((str:concat (model:api-root) "/v2/quotas") ((http:credential "OBSERVE_SYNTHETIC")) "SYNTHETIC_API_KEY"))
; The provider's JSON object for KIND, or (ModelObservationFailed reason).
(= (model:fetch $kind)
   (let ($url $headers $key) (model:request $kind)
     (if (not (model:env-set? $key))
       (ModelObservationFailed (if (== $kind "kimi-models") "Kimi Code credential is not configured" (str:concat $key " is not set")))
       (let $deadline (model:introspect-ms)
         (case (io:try-submit (http:request "GET" $url $headers "" $deadline 4194304))
           (((io:pending $id) (model:received (model:await $id)))
            ($refused (ModelObservationFailed "submit-error"))))))))
(= (model:await $id)
   (case (io:try-wait $id 250) (((io:idle) (model:await $id)) ((io:event $received $result) $result) ($other $other))))
(= (model:parse-error? $value)
   (if (== (get-metatype $value) Expression) (== (car-atom $value) JsonPythonErrorV1) False))
(= (model:received $result)
   (case $result
     (((http:response-with-metadata $status $body $metadata) (model:received (http:response $status $body)))
      ((http:response $status $body)
       (if (>= $status 400) (ModelObservationFailed (str:concat "HTTP " (repr $status)))
         (let $payload (json:parse-python $body)
           (case $payload
             (((JsonObject $pairs)
               (case (json:get-first $payload "error")
                 (((JsonFoundV1 $index $error) (ModelObservationFailed (model:python-str $error))) ($missing $payload))))
              ($other (ModelObservationFailed (if (model:parse-error? $other) "bad response" "unexpected response"))))))))
      ($other (ModelObservationFailed "transport error")))))
; A catalog keeps each model's id, once, and its context window; Kimi Code's
; ids carry the kimi: prefix the menu selects them by.
(= (model:catalog $payload $prefix)
   (case (json:get $payload "data")
     (((JsonArray $rows)
       (let* (($named (model:named-rows $rows)) ($ids (model:named-ids $named)) ($order (unique-atom $ids)))
         (JsonArray (if (== (size-atom $order) (size-atom $ids))
                      (map-atom $named $pair (let ($id $row) $pair (model:catalog-row $id $row $prefix)))
                      (model:first-rows $named $order $prefix)))))
      ($other (JsonArray ())))))
; (id row) for each row that is an object with an id, the id trimmed.
(= (model:named-rows $rows) (collapse (let $row (superpose $rows) (model:named-row $row))))
(= (model:named-row $row)
   (case $row
     (((JsonObject $pairs)
       (let* (($given (json:get $row "id"))
              ($id (str:strip-python (if (model:truthy? $given) (model:python-str $given) ""))))
         (if (== $id "") (empty) ($id $row))))
      ($other (empty)))))
(= (model:named-ids $named) (map-atom $named $pair (car-atom $pair)))
; Each id's first row, in order, when the provider repeated an id: walking the
; rows, an id's first row is the one that meets it at the head of the ids
; still to emit. Consing makes this quadratic in the rows, so it is kept for
; that case; a catalog without repeats is mapped whole.
(= (model:first-rows $named $order $prefix)
   (if (or (== $named ()) (== $order ())) ()
     (let (($id $row) $rest) ((car-atom $named) (cdr-atom $named))
       (if (== $id (car-atom $order))
         (cons-atom (model:catalog-row $id $row $prefix) (model:first-rows $rest (cdr-atom $order) $prefix))
         (model:first-rows $rest $order $prefix)))))
(= (model:catalog-row $id $row $prefix)
   (let* (($length (json:get $row "context_length"))
          ($context (if (model:truthy? $length) $length (json:get $row "context_window")))
          ($named (if (== $prefix "") $id
                    (str:concat $prefix (if (str:starts-with $id $prefix)
                                          (str:byte-slice $id (str:byte-length $prefix) (str:byte-length $id)) $id)))))
     (JsonObject ((JsonPair "id" $named) (JsonPair "context" $context)))))
; The quota keeps only the numbers the quota line shows.
(= (model:quota-value $payload)
   (JsonObject (model:append (model:subset $payload "planWeeklyLimit" ("percentRemaining" "nextRegenAt" "nextRegenPercent"))
                 (model:append (model:subset $payload "weeklyTokenLimit" ("remainingCredits" "maxCredits" "percentRemaining"))
                   (model:subset $payload "rollingFiveHourLimit" ("remaining" "max" "limited"))))))
(= (model:subset $payload $key $fields)
   (case (json:get $payload $key)
     (((JsonObject $pairs) ((JsonPair $key (JsonObject (model:fields (JsonObject $pairs) $fields))))) ($other ()))))
(= (model:fields $object $fields)
   (if (== $fields ()) ()
     (cons-atom (JsonPair (car-atom $fields) (json:get $object (car-atom $fields))) (model:fields $object (cdr-atom $fields)))))
(= (model:value "models" $payload) (model:catalog $payload ""))
(= (model:value "kimi-models" $payload) (model:catalog $payload "kimi:"))
(= (model:value "quota" $payload) (model:quota-value $payload))
(= (model:clip $text $limit) (if (> (str:char-length $text) $limit) (str:char-slice $text 0 $limit) $text))
; Read KIND from its provider and record it whole, or note the failure and
; keep the last good value. True when the file was written.
(= (model:observe $kind)
   (let* (($fetched (model:fetch $kind)) ($now (system:wall-time)))
     (case $fetched
       (((ModelObservationFailed $why) (model:update $kind (("attempted_at" $now) ("last_error" (model:clip $why 160))) False))
        ($payload (model:update $kind (("observed_at" $now) ("attempted_at" $now) ("value" (model:value $kind $payload))) True))))))
