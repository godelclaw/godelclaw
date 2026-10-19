; Select once; native tmux calls cross only the process boundary.
!(import! &self ./src/engine_modes.py)
(= (tmux-io:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (tmux-io:cetta? (py-call (engine_modes.active_engine)))
     (import! &self ./src/tmux_cetta)
     (import! &self ./src/tmux_python))
