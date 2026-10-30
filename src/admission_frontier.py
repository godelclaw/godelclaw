"""Typed failure labels at preparation/provider boundaries; no execution policy."""
import re

DENIAL_CODES = frozenset({"cognition-paused", "provider-cooldown", "native-call-capacity"})

class AdmissionDenied(RuntimeError):
    def __init__(self, code):
        self.reason_code = str(code)
        super().__init__(self.reason_code)


class ContextPreparationFailure(RuntimeError):
    """A local preparation failure, before provider admission.

    Only an owned code crosses the diagnostic boundary. Exception messages,
    URLs and provider bodies never become an arbitrary displayed reason.
    """
    def __init__(self, code):
        text = str(code)
        self.reason_code = (text if len(text) <= 64 and
            re.fullmatch(r"context-[a-z0-9]+(?:-[a-z0-9]+)*", text)
            else "context-preparation-failed")
        super().__init__(self.reason_code)


def failure_code(error):
    return (error.reason_code if isinstance(error, (AdmissionDenied, ContextPreparationFailure))
            else type(error).__name__)
