; Skill routing only; never import native libraries into the SWI route.
!(import! &self ./src/engine_modes.py)
!(case (py-call (engine_modes.active_engine))
   (("cetta" (import! &self ./src/goal_views_cetta.metta))
    (cetta (import! &self ./src/goal_views_cetta.metta))
    ($other (import! &self ./src/goal_views_python.metta))))
