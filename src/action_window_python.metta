; Unchanged Python oracle on SWI-PeTTa.
(= (action-window:history-path)
   (py-call (helper.path_from_env "METTACLAW_HISTORY_PATH" "./memory/history.metta")))
(= (action-window:recent $path $limit) (py-call (helper.recent_actions $path $limit)))
(= (action-window:files $path $limit) (py-call (helper.relevant_files $path $limit)))
