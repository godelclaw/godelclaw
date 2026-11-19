!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

;; The context sources each prompt carries, as the native loop assembles them.
!(let $bundle (addition-context-sources)
   (if (loop-host:nonempty? $bundle)
       (println! CONTEXT_SOURCES_OK)
       (println! CONTEXT_SOURCES_EMPTY)))
