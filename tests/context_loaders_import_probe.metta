; Preparing the native context bundle must not need coding_estate: the native
; route loads coding-sessions itself (estate:load), so deleting the Python
; module cannot break preparation. Run on the SDK engine.
!(import! &self (library lib_import))
!(import! &self (library lib_llm))
!(import! &self ./native_test_support.metta)
!(py-eval "__import__('sys').modules.__setitem__('coding_estate', None)")
!(import! &self ../src/context_source_loaders.py)
!(let $prepared (py-call (context_source_loaders.prepare ()))
   (nt:require prepare-without-coding-estate (nt:equal (py-eval "int(isinstance(__import__('context_source_loaders').prepare(()), tuple))") 1)))
!(println! CONTEXT_LOADERS_IMPORT_OK)
