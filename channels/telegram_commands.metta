; An agent's operator commands, as the CeTTa Telegram channel service reads
; them (--commands). Declarations only; the service refuses anything else.
; This file is shared by every agent. src/start_telegram_service.py writes each
; agent's own copy with (tg-cmd:agent NAME) from METTACLAW_AGENT_NAME.
;
; help, stop and start are the service's own: it answers them at once, and
; stop holds everything the agent submits until start. Every delegated command
; is asked of the agent's command responder, never of its cognition; if that
; answer is late, the service replies with a notice and edits it when the
; answer arrives. The menus answer with buttons; a tap on one is asked of the
; responder the same way and answered within the same deadline.

(tg-cmd:command "/help" help "List these commands")
(tg-cmd:command "/stop" stop "Stop cognition and hold everything the agent sends")
(tg-cmd:command "/start" start "Enable cognition and let the agent's messages out again")
(tg-cmd:command "/wake" delegated "End the current rest")
(tg-cmd:command "/activity" delegated "Show whether cognition is working, resting, or idle")
(tg-cmd:command "/health" delegated "Show runtime, channel, and memory health")
(tg-cmd:command "/engine" delegated "Show or switch the evaluator engine")
(tg-cmd:command "/fuel" delegated "Show or switch what a renewal does to the budget")
(tg-cmd:command "/mode" delegated "Show or switch the cognitive loop mode")
(tg-cmd:command "/model" delegated "Show or switch the language model")
(tg-cmd:command "/engines" delegated "Evaluator engines, as buttons")
(tg-cmd:command "/fuels" delegated "Fuel disciplines, as buttons")
(tg-cmd:command "/modes" delegated "Cognitive loop modes, as buttons")
(tg-cmd:command "/models" delegated "Language models, as buttons")
(tg-cmd:command "/energy" delegated "Arming energy per sender, as buttons")
(tg-cmd:command "/quota" delegated "Show model budget")
(tg-cmd:command "/delete" delegated "Delete a message: /delete chat message-id")
(tg-cmd:command "/claude_code_authorization" delegated "Toggle Claude Code authorization")
