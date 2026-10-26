; Select once: native fuel discipline on CeTTa, the Python module elsewhere.
!(import! &self ./src/engine_modes.metta)
(= (fuel-io:cetta $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (fuel-io:cetta (engine:active))
     (import! &self ./src/fuel_cetta)
     (import! &self ./src/fuel_python))
