; Select once; native tmux calls cross only the process boundary.
!(import! &self ./src/engine_modes.metta)
(= (tmux-io:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (tmux-io:cetta? (engine:active))
     (import! &self ./src/tmux_cetta)
     (import! &self ./src/tmux_python))
