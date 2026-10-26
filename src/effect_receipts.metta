; Engine-selected boundary. SWI imports no native receipt libraries.
!(import! &self ./src/engine_modes.py)
!(case (py-call (engine_modes.active_engine))
   (("cetta" (import! &self ./src/effect_receipts_native.metta))
    (cetta (import! &self ./src/effect_receipts_native.metta))
    ($other (import! &self ./src/effect_receipts_python.metta))))
