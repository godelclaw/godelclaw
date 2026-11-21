!(import! &self (library lib_prolog))
!(import! &self ./core.metta)
!(let $done (catch (iter:start))
   (progn (mode-host:stop-ledger) $done))
