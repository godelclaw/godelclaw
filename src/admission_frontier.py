"""Compatibility error labels for provider adapters; no execution policy."""
DENIAL_CODES = frozenset({"cognition-paused", "provider-cooldown", "native-call-capacity"})

class AdmissionDenied(RuntimeError):
    def __init__(self, code):
        self.reason_code = str(code)
        super().__init__(self.reason_code)

def failure_code(error):
    return error.reason_code if isinstance(error, AdmissionDenied) else type(error).__name__
