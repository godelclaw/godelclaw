!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

;; No provider or channel traffic. Drive the real client in one process so
;; its prepared frontier survives between the initial turn and its retry.
!(py-eval "__import__('os').environ.update(METTACLAW_TELEGRAM_SERVICE_SOCKET='', METTACLAW_TELEGRAM_LOG_PATH=__import__('os').path.join(__import__('os').path.dirname(__import__('os').environ['METTACLAW_ENGINE_STATE_PATH']), 'batch-probe.jsonl'))")
!(tg:activity-add "" "42" "earlier input" False "full" "101" False)
!(tg:activity-batch)
!(assert (== (tg:prepared-count) 1))
!(tg:activity-add "" "42" "steering input" False "full" "102" False)
!(assert (== (tg:pending-count) 2))
!(assert (== (tg:prepared-count) 1))
!(tg:activity-batch)
!(assert (== (tg:prepared-count) 2))
!(py-eval "__import__('builtins').exec(\"assert __import__('telegram')._current_batch_update_ids == {'101', '102'}\")")
;; An arrival after preparation belongs to the next turn, even when the
;; successful earlier turn acknowledges its fixed prepared presentation.
!(tg:activity-add "" "42" "later input" False "full" "103" False)
!(assert (== (tg:prepared-count) 2))
!(assert (== (tg:ack-activity-batch) 1))
!(assert (== (tg:pending-count) 1))
!(tg:activity-batch)
!(assert (== (tg:prepared-count) 1))
!(py-eval "__import__('builtins').exec(\"assert __import__('telegram')._current_batch_update_ids == {'103'}\")")
!(tg:ack-activity-batch)
!(assert (== (tg:pending-count) 0))
!(println! INPUT_BATCH_REFRESH_OK)
