; Select once: native fuel discipline on CeTTa, the Python module elsewhere.
!(import! &self ./src/engine_modes.py)
(= (fuel-io:cetta $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (fuel-io:cetta (py-call (engine_modes.active_engine)))
     (import! &self ./src/fuel_cetta)
     (import! &self ./src/fuel_python))
