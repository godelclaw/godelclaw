; Run the journal retention (scripts/telegram_journal_retention.metta) with
; this program's arguments; exit 1 when it fails or a unit is not started again.
!(import! &self ./telegram_journal_retention.metta)
!(case (jr:main (system:args))
   (((Error $where $why) (let $_ (jr:log (format-args "journal retention failed: {} {}" ($where (repr $why)))) (system:exit 1)))
    ((JrNotRestarted $why $result)
     (let $_ (jr:log (format-args "journal retention: a unit was not started again: {}; the run: {}" ((repr $why) (repr $result)))) (system:exit 1)))
    ($done (println! $done))))
