; Skill routing only; never import native libraries into the SWI route.
!(import! &self ./src/engine_modes.metta)
!(case (engine:active)
   (("cetta" (import! &self ./src/goal_views_cetta.metta))
    (cetta (import! &self ./src/goal_views_cetta.metta))
    ($other (import! &self ./src/goal_views_python.metta))))
