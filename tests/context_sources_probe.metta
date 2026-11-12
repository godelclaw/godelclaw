!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

!(let $bundle (py-call (context_sources.bundle))
   (if (loop-host:nonempty? $bundle)
       (println! CONTEXT_SOURCES_OK)
       (println! CONTEXT_SOURCES_EMPTY)))
