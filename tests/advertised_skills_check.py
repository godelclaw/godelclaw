"""Structured requests advertise one skill a line."""


def _safe(text):
    # string-safe, as both engines' utilities write it.
    return (text.replace('""', "_quote_").replace("\n", "_newline_")
            .replace("'", "_apostrophe_"))


def one_skill_a_line(prompt, skills):
    """1 when the advertisement separates every skill by exactly one line break."""
    prompt, skills = str(prompt), [str(skill) for skill in skills]
    start = prompt.index("ADVERTISED_CAPABILITIES:")
    end = prompt.find("\n\n", start)
    section = prompt[start:] if end < 0 else prompt[start:end]
    inner = sum(_safe(skill).count("_newline_") for skill in skills)
    return int(len(skills) > 1 and section.count("_newline_") == len(skills) - 1 + inner)
