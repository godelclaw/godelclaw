; Native transport fixture: emit the supplied bytes without adding a newline.
!(import! &self (library fs))
!(import! &self (library system))
!(import! &self ../../src/cli_values_cetta.metta)
!(system:write stdout (fs:read-text (cli:text (system:arg 0))))
