; A received body the engine refused to keep is named by its reason; any other
; engine error stays a failed wait, never a named refusal.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/native_transport_cetta.metta)
!(nt:require too-large-named
   (nt:equal (llm-transport:refused 7 (http:error response-too-large 1048576))
             (io:event 7 (NativeHttpRefusedV1 response-too-large 1048576))))
!(nt:require budget-named
   (nt:equal (llm-transport:refused 7 (http:error response-budget-exceeded))
             (io:event 7 (NativeHttpRefusedV1 response-budget-exceeded 0))))
!(nt:require non-text-named
   (nt:equal (llm-transport:refused 7 (http:error non-text-response)) (io:event 7 (NativeHttpRefusedV1 non-text-response 0))))
!(nt:require credential-named
   (nt:equal (llm-transport:refused 7 (http:error credential-in-response))
             (io:event 7 (NativeHttpRefusedV1 credential-in-response 0))))
!(nt:require unknown-reason-failed (nt:equal (llm-transport:refused 7 (http:error something-else)) (io:failed)))
!(nt:require other-error-failed (nt:equal (llm-transport:refused 7 (bad-argument x)) (io:failed)))
!(println! UNIT_NATIVE_TRANSPORT_OK)
