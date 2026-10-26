; Owned native filesystem operations. Every acquired writer is explicitly
; closed; locks are explicitly released, including after errors-as-values.
; These helpers take pre-encoded valid payloads. They are NOT public-worklog
; transactions or an implementation of json.dump's incremental failure effects.
; Callers must preserve their own encoding/open order and whole-root lock scope.
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self (library math))
!(import! &self (library list))
!(import! &self ./src/effect_receipt_values_cetta.metta)

(= (erio:class $errno)
   (case $errno ((1 PermissionError) (2 FileNotFoundError) (13 PermissionError)
     (17 FileExistsError) (20 NotADirectoryError) (21 IsADirectoryError) ($other OSError))))
(= (erio:result $value)
   (case $value
     (((fs:failed $kind $operation $path $errno $progress $message)
       (ErFailure (erio:class $errno) $message $value))
      ((Error $source $reason)
       (ErFailure NativeEvaluationError "native filesystem evaluation failed" $value))
      ($other $other))))
(= (erio:single $answers)
   (case $answers
     ((($value) (erio:result $value))
      ($other (ErFailure NativeEvaluationError "native operation did not return one outcome" $answers)))))
(= (erio:open $path $flags $mode)
   (let $value (erio:single (collapse (catch (fs:open-write $path $flags $mode))))
     (case $value
       (((NativeHandle "file-writer" $id) $value)
        ((ErFailure $_ $_ $_) $value)
        ($other (ErFailure NativeCapabilityError "missing writer owner" $other))))))
(= (erio:mkdir $path $mode)
   (let $value (erio:single (collapse (catch (fs:make-directories $path $mode))))
     (case $value ((() ()) ((ErFailure $_ $_ $_) $value)
       ($other (ErFailure NativeCapabilityError "missing mkdir outcome" $other))))))
(= (erio:lock $owner)
   (let $value (erio:single (collapse (catch (fs:lock $owner exclusive))))
     (case $value ((fs:locked fs:locked) ((ErFailure $_ $_ $_) $value)
       ($other (ErFailure NativeCapabilityError "missing lock outcome" $other))))))
(= (erio:write $owner $payload)
   (let $value (erio:single (collapse (catch (fs:write-all $owner $payload))))
     (case $value
       (((fs:written $n) (if (== $n (str:byte-length $payload)) $value
           (ErFailure NativeCapabilityError "incomplete native write outcome" $value)))
        ((ErFailure $_ $_ $_) $value)
        ($other (ErFailure NativeCapabilityError "missing write outcome" $other))))))
(= (erio:sync $owner)
   (let $value (erio:single (collapse (catch (fs:sync $owner))))
     (case $value ((fs:synced fs:synced) ((ErFailure $_ $_ $_) $value)
       ($other (ErFailure NativeCapabilityError "missing fsync outcome" $other))))))
; A cleanup error overrides the body, as a Python finally/with close can do.
; The previous outcome is retained in detail: uncertain effects are not replayed.
(= (erio:cleanup-outcome $previous $cleanup)
   (case $cleanup
     (((ErFailure $class $message $detail)
       (ErFailure $class $message (CleanupFailure $detail $previous)))
      ($success $previous))))
(= (erio:finish $owner $locked $result)
   (let* (($unlocked (if $locked
                     (erio:single (collapse (catch (fs:unlock $owner)))) fs:unlocked))
          ($after-unlock (case $unlocked
            ((fs:unlocked $result)
             ((ErFailure $_ $_ $_) (erio:cleanup-outcome $result $unlocked))
             ($other (ErFailure NativeCapabilityError "missing unlock outcome" ($other $result))))))
          ($closed (erio:single (collapse (catch (fs:close $owner))))))
     (case $closed
       ((fs:closed $after-unlock)
        ((ErFailure $_ $_ $_) (erio:cleanup-outcome $after-unlock $closed))
        ($other (ErFailure NativeCapabilityError "missing close outcome" ($other $after-unlock)))))))
(= (erio:write-sync $owner $payload)
   (let $written (erio:write $owner $payload)
     (if (erv:failed? $written) $written
       (let $synced (erio:sync $owner) (if (erv:failed? $synced) $synced 1)))))
(= (erio:append $path $payload $lock)
   (let $owner (erio:open $path ("append" "create") 384)
     (if (erv:failed? $owner) $owner
       (let* (($admitted (if $lock (erio:lock $owner) fs:locked))
              ($result (if (erv:failed? $admitted) $admitted
                (erio:single (collapse (catch (erio:write-sync $owner $payload)))))))
         (erio:finish $owner $lock $result)))))
(= (erio:write-state $temporary $path $payload)
   (let $owner (erio:open $temporary ("truncate" "create") 384)
     (if (erv:failed? $owner) $owner
       (let* (($result (erio:single (collapse (catch (erio:write-sync $owner $payload)))))
              ($closed (erio:finish $owner False $result)))
         (if (erv:failed? $closed) $closed
           (let $renamed (erio:single (collapse (catch (fs:rename $temporary $path))))
             (case $renamed
               ((() 1) ((ErFailure $_ $_ $_) $renamed)
                ($other (ErFailure NativeCapabilityError "missing rename outcome" $other))))))))))
; pathlib's lexical POSIX path construction: collapse '.'/empty parts, NOT '..'.
; path:normalize would incorrectly change symlink/.. filesystem authority.
(= (erio:prefix $path)
   (if (and (str:starts-with $path "//") (not (str:starts-with $path "///"))) "//"
     (if (str:starts-with $path "/") "/" "")))
(= (erio:parts $path)
   (filter-atom (str:split "/" $path) $part (and (not (== $part "")) (not (== $part ".")))))
(= (erio:build-path $prefix $parts)
   (let $body (str:join "/" $parts)
     (if (== $body "") (if (== $prefix "") "." $prefix) (str:concat $prefix $body))))
(= (erio:path $path) (erio:build-path (erio:prefix $path) (erio:parts $path)))
(= (erio:parent $path)
   (let $parts (erio:parts $path)
     (erio:build-path (erio:prefix $path) (erv:take $parts (- (size-atom $parts) 1)))))
(= (erio:join $root $leaf)
   (if (str:starts-with $leaf "/") (erio:path $leaf)
     (erio:path (str:join "/" ($root $leaf)))))
(= (erio:universal $text)
   (str:join "\n" (str:split "\r" (str:join "\n" (str:split "\r\n" $text)))))
(= (erio:lines $text)
   (let* (($initial (str:line-new))
          ((LinesCollectedV1 $lines $_) (str:feed-lines $initial $text True)))
     (erio:line-values $lines)))
(= (erio:line-values $lines)
   (case $lines
     ((LNil ())
      ((LCons (LineV1 $_ $text) $tail) (cons-atom $text (erio:line-values $tail)))
      ($other (ErFailure NativeCapabilityError "missing line observation" $other)))))

; fromtimestamp rounds fractional seconds to microseconds, ties to even,
; BEFORE ISO seconds truncation. Native format-time alone truncates to time_t.
(= (erio:round-even $value)
   (let* (($absolute (math:abs $value)) ($whole (math:floor $absolute))
          ($fraction (- $absolute $whole)))
     (* (if (< $value 0) -1 1)
       (+ $whole (if (or (> $fraction 0.5) (and (== $fraction 0.5) (== (% $whole 2) 1))) 1 0)))))
(= (erio:timestamp-seconds $timestamp)
   (if (math:isnan $timestamp) (erv:fail ValueError "Invalid value NaN (not a number)")
     (if (math:isinf $timestamp) (erv:fail OverflowError "timestamp out of range for platform time_t")
       (let* (($whole (math:trunc $timestamp))
              ($microseconds (erio:round-even (* (- $timestamp $whole) 1000000))))
         (+ $whole (if (< $microseconds 0) -1 (if (>= $microseconds 1000000) 1 0)))))))
(= (erio:pad $n $width)
   (let $value (repr $n)
     (if (>= (str:byte-length $value) $width) $value
       (str:concat "0" (erio:pad $n (- $width 1))))))
(= (erio:calendar $timestamp $zone)
   (let $text (system:format-time "%Y|%m|%d|%H|%M|%S" $timestamp $zone)
     (if (== (get-type $text) String)
       (map-atom (str:split "|" $text) $field (erv:int-string $field))
       (ErFailure NativeClockError "native calendar formatting failed" $text))))
(= (erio:leap $year)
   (and (== (% $year 4) 0) (or (not (== (% $year 100) 0)) (== (% $year 400) 0))))
(= (erio:civil-seconds $calendar)
   (let* ((($year $month $day $hour $minute $second) $calendar)
          ($before (- $year 1))
          ($days (+ (+ (- (+ (* $before 365) (math:floor (/ $before 4)))
                        (math:floor (/ $before 100))) (math:floor (/ $before 400)))
                    (+ (index-atom (0 31 59 90 120 151 181 212 243 273 304 334) (- $month 1))
                       (+ (- $day 1) (if (and (> $month 2) (erio:leap $year)) 1 0))))))
     (+ (* $days 86400) (+ (* $hour 3600) (+ (* $minute 60) $second)))))
(= (erio:offset-text $offset)
   (let* (($absolute (math:abs $offset)) ($hours (math:floor (/ $absolute 3600)))
          ($minutes (math:floor (/ (% $absolute 3600) 60))) ($seconds (% $absolute 60)))
     (str:join "" ((if (< $offset 0) "-" "+") (erio:pad $hours 2) ":" (erio:pad $minutes 2)
       (if (== $seconds 0) "" (str:concat ":" (erio:pad $seconds 2)))))))
(= (erio:iso-calendar $calendar $offset)
   (let ($year $month $day $hour $minute $second) $calendar
     (if (or (< $year 1) (> $year 9999))
       (erv:fail ValueError (str:join "" ("year " (repr $year) " is out of range")))
       (if (>= (math:abs $offset) 86400)
         (erv:fail NativeClockError "unqualified local UTC offset outside datetime's permitted range")
         (str:join "" ((erio:pad $year 4) "-" (erio:pad $month 2) "-" (erio:pad $day 2) "T"
           (erio:pad $hour 2) ":" (erio:pad $minute 2) ":" (erio:pad $second 2) (erio:offset-text $offset)))))))
(= (erio:iso-seconds $seconds $zone)
   (let $calendar (erio:calendar $seconds $zone)
     (if (erv:failed? $calendar) $calendar
       (if (== $zone "utc") (erio:iso-calendar $calendar 0)
         (let $utc (erio:calendar $seconds "utc")
           (if (erv:failed? $utc) $utc
             (erio:iso-calendar $calendar (- (erio:civil-seconds $calendar) (erio:civil-seconds $utc)))))))))
(= (erio:iso $timestamp $zone)
   (let $seconds (erio:timestamp-seconds $timestamp)
     (if (erv:failed? $seconds) $seconds
       (if (== $zone "utc") (erio:iso-seconds $seconds $zone)
         (let* (($utc (erio:calendar $seconds "utc"))
                ($local (erio:calendar $seconds "local"))
                ($utc-year (car-atom $utc)) ($local-year (car-atom $local)))
           (if (or (< $utc-year 1) (> $utc-year 9999))
             (erv:fail ValueError (str:join "" ("year " (repr $utc-year) " is out of range")))
             (if (or (< $local-year 1) (> $local-year 9999)) (erv:fail OverflowError "date value out of range")
               (erio:iso-calendar $local (- (erio:civil-seconds $local) (erio:civil-seconds $utc))))))))))
