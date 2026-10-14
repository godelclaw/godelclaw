; Existing launch selection; the new hierarchy itself is native MeTTa/CeTTa.
!(if (file-io:cetta? (py-call (engine_modes.active_engine)))
     (import! &self ./src/hierarchical_memory_cetta)
     (import! &self ./src/hierarchical_memory_unavailable))
