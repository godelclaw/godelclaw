"""The prompt-layout invariant that makes provider prefix caching work.

Two requests from consecutive turns must agree on everything before the last
cache boundary, and nothing that changes every turn may appear there.  The
stable head (prompt, skills, output format, context order) comes before the
first boundary.  Used by prompt_cache_layout_probe.metta on the real renderer.
"""

import json

BOUNDARY = "_cache_boundary_"
LAYOUT_BOUNDARIES = 3
HEAD = ("PROMPT:", "OUTPUT_FORMAT:", "CONTEXT_ORDER:")
SKILLS = ("SKILLS:", "ADVERTISED_CAPABILITIES:")
PER_TURN = (
    "LOOPS_LEFT:", "SLEEP_INTERVAL_SECONDS:", "ACTIVE_MODE:",
    "ACTIVE_MODEL:", "ATTENTION_DAG:", "LAST_SKILL_USE_RESULTS:", "WAKE:",
    "TIME:",
    "NEW_ACTIVITY:", "CONTEXT_CERTIFICATE",
)


def violations(first, second):
    """Everything wrong with the pair, as short strings; empty when sound."""
    first, second = str(first), str(second)
    problems = []
    if first.startswith('{') and second.startswith('{'):
        a, b = json.loads(first), json.loads(second)
        if a.get('kind') == b.get('kind') == 'role-context-v1':
            trusted_a = [m for m in a['messages'] if m['role'] in ('system', 'developer')]
            trusted_b = [m for m in b['messages'] if m['role'] in ('system', 'developer')]
            if trusted_a != trusted_b:
                problems.append('authored instruction prefix differs')
            head = '\n'.join(m['content'] for m in trusted_a)
            problems += ['instruction prefix lacks ' + mark for mark in HEAD if mark not in head]
            if not any(mark in head for mark in SKILLS):
                problems.append('instruction prefix lacks skills')
            problems += ['per-turn ' + mark + ' in instructions' for mark in PER_TURN if mark in head]
            return problems
    for name, text in (("first", first), ("second", second)):
        if text.count(BOUNDARY) != LAYOUT_BOUNDARIES:
            problems.append("%s has %d boundaries" % (
                name, text.count(BOUNDARY)))
    if problems:
        return problems
    stable = first[:first.rindex(BOUNDARY)]
    if stable != second[:second.rindex(BOUNDARY)]:
        problems.append("the prefix before the last boundary differs")
    head = first[:first.index(BOUNDARY)]
    problems += ["head lacks " + mark for mark in HEAD if mark not in head]
    if not any(mark in head for mark in SKILLS):
        problems.append("head lacks the skills")
    problems += ["per-turn " + mark + " before the last boundary"
                 for mark in PER_TURN if mark in stable]
    return problems


def ok(first, second):
    problems = violations(first, second)
    if problems:
        print("PROMPT_LAYOUT_VIOLATIONS", problems)
    return 0 if problems else 1
