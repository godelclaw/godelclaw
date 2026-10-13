"""Messages queue at the next turn boundary; only operator stop interrupts."""

def enabled():
    return True

def revision():
    return 0

def ingress(identity, role, update):
    return 0

def observe_pending(tasks):
    return 1

def description():
    return "New messages queue for the next turn. Operator /stop suspends cognition at a command boundary."
