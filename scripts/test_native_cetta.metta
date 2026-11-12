; Native qualification entrypoint. External fixture programs supply I/O only.
!(import! &self ../tests/provider_request_contracts.metta)
!(nt:require contract-driver-completed (nt:equal (collapse (request-test:run)) (25)))
!(println! NATIVE_REQUEST_CONTRACTS_PASSED)
