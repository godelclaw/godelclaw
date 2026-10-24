; SWI-PeTTa retains the unchanged Python bundle and projector.
!(import! &self ./src/context_sources.py)
(= (context-bundle:with-views $views)
   (py-call (context_sources.bundle_with_views $views)))
(= (context-bundle:with-views $views $conversation)
   (py-call (context_sources.bundle_with_views $views $conversation)))
