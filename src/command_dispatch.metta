; The interpreter is shared; only its environment/string primitives differ.
!(progn (import! &self ./src/command_dispatch_cetta)
            (import! &self ./src/command_dispatch_hooks)
            (import! &self ./src/command_dispatch_core))
