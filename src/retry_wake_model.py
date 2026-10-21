"""Generated from verification/RetryWake.module; do not edit."""
MODULE_SHA256 = '486ffdd2f00d41837259928ab4b4173443e210cb42f22fea6af676227236724c'
ACTIVE = {'absent': False, 'claimed': True, 'submitted': True, 'completed': False, 'failed': False, 'uncertain': False}
TERMINAL = {'pending': False, 'completed': True, 'failed': True, 'unknown': True}
TRANSITIONS = {('completed', 'absent'): 'absent',
 ('completed', 'claimed'): 'completed',
 ('completed', 'completed'): 'completed',
 ('completed', 'failed'): 'failed',
 ('completed', 'submitted'): 'completed',
 ('completed', 'uncertain'): 'uncertain',
 ('failed', 'absent'): 'absent',
 ('failed', 'claimed'): 'failed',
 ('failed', 'completed'): 'completed',
 ('failed', 'failed'): 'failed',
 ('failed', 'submitted'): 'uncertain',
 ('failed', 'uncertain'): 'uncertain',
 ('pending', 'absent'): 'absent',
 ('pending', 'claimed'): 'claimed',
 ('pending', 'completed'): 'completed',
 ('pending', 'failed'): 'failed',
 ('pending', 'submitted'): 'submitted',
 ('pending', 'uncertain'): 'uncertain',
 ('unknown', 'absent'): 'absent',
 ('unknown', 'claimed'): 'uncertain',
 ('unknown', 'completed'): 'completed',
 ('unknown', 'failed'): 'failed',
 ('unknown', 'submitted'): 'uncertain',
 ('unknown', 'uncertain'): 'uncertain'}

def reconcile(request, call):
    """Return the model result, or None for an unmodelled state."""
    return TRANSITIONS.get((request, call))
