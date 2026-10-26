; Existing launch selection; the new hierarchy itself is native MeTTa/CeTTa.
!(import! &self ./src/engine_modes.metta)
!(if (file-io:cetta? (engine:active))
     (import! &self ./src/hierarchical_memory_cetta)
     (import! &self ./src/hierarchical_memory_unavailable))
