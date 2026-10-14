import json
import os
import sys
import urllib.error
import urllib.request


def _session_memory():
    tools_dir = os.environ.get("SESSION_MEMORY_TOOLS", "")
    if tools_dir and tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        import session_memory as sm
    except ImportError as exc:
        raise RuntimeError(
            "session_memory is unavailable; set SESSION_MEMORY_TOOLS or PYTHONPATH"
        ) from exc
    return sm
DIM = int(os.environ.get("METTACLAW_EMBED_DIM", "1024"))
DEVICE = os.environ.get("METTACLAW_EMBED_DEVICE", "auto")
BATCH_SIZE = int(os.environ.get("METTACLAW_EMBED_BATCH_SIZE", "1"))
MAX_TOKENS = int(os.environ.get("METTACLAW_EMBED_MAX_TOKENS", "4096"))
QUERY_PROMPT_NAME = os.environ.get("METTACLAW_EMBED_QUERY_PROMPT_NAME", "query")
NORMALIZE = os.environ.get("METTACLAW_EMBED_NORMALIZE", "1").lower() not in {
    "0",
    "false",
    "no",
}
PROTOCOL = os.environ.get(
    "METTACLAW_EMBED_PROTOCOL", "qwen3-embedding-8b-1024-v1"
)
# Shared embedding daemon endpoint. When set, embeddings come from ONE
# resident model process instead of loading the 15GB model in-process —
# multiple claws each loading a copy is exactly what OOMs the GPU.
ENDPOINT = os.environ.get("METTACLAW_EMBED_ENDPOINT", "")


def _embed_remote(text, is_query):
    payload = json.dumps(
        {"texts": [str(text)], "kind": "query" if is_query else "document"}
    ).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT.rstrip("/") + "/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"embedding daemon error {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(
            f"embedding daemon unreachable at {ENDPOINT} ({exc}); "
            "start it with: systemctl --user start qwen-embed-daemon "
            "— do NOT load the model in-process."
        ) from exc
    if body.get("protocol") != PROTOCOL:
        raise RuntimeError(
            f"embedding protocol mismatch: {body.get('protocol')!r} != {PROTOCOL!r}"
        )
    if body.get("dim") != DIM:
        raise RuntimeError(f"embedding dimension mismatch: {body.get('dim')!r} != {DIM}")
    vectors = body.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != 1:
        raise RuntimeError("embedding daemon returned an invalid vector batch")
    vector = vectors[0]
    if (
        not isinstance(vector, list)
        or len(vector) != DIM
        or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   for value in vector)
    ):
        raise RuntimeError("embedding daemon returned an invalid vector")
    return vector


def _embed_local(text, is_query):
    model = os.environ.get("METTACLAW_EMBED_MODEL", "")
    if not model:
        raise RuntimeError("METTACLAW_EMBED_MODEL is not set")
    sm = _session_memory()
    return sm.sentence_transformer_embed(
        model_name_or_path=model,
        texts=[str(text)],
        is_query=is_query,
        truncate_dim=DIM,
        device=DEVICE,
        batch_size=BATCH_SIZE,
        max_tokens_per_text=MAX_TOKENS,
        normalize_embeddings=NORMALIZE,
        query_prompt_name=QUERY_PROMPT_NAME,
    )[0]


def _embed(text, is_query):
    if ENDPOINT:
        return _embed_remote(text, is_query)
    return _embed_local(text, is_query)


def embed_document(text):
    return _embed(text, False)


def embed_query(text):
    return _embed(text, True)


# Guarded composites for the agent loop: a dead embedding daemon must yield
# an instructive RESULT, not a generic command error. "Can't retrieve" and
# "doesn't exist" are different facts; conflating them has produced false
# "the work was never done" conclusions.
DAEMON_DOWN_QUERY = (
    "EMBED_DAEMON_DOWN: semantic memory is UNAVAILABLE, this query was not "
    "run. Do NOT infer absence — files, history, and the wiki still hold "
    "the truth; use shell/grep now and retry query after the daemon returns.")
DAEMON_DOWN_REMEMBER = (
    "EMBED_DAEMON_DOWN: this remember was NOT saved. Preserve it another "
    "way (pin, file, or history) and re-remember after the daemon returns.")


def _daemon_down(exc):
    return "daemon unreachable" in str(exc) or "daemon error" in str(exc)


def query_guarded(text, k):
    try:
        embedding = embed_query(text)
    except RuntimeError as exc:
        if _daemon_down(exc):
            return DAEMON_DOWN_QUERY
        raise
    import lib_chromadb
    return lib_chromadb.query_with_ids(embedding, k)


def remember_guarded(text, timestamp):
    try:
        embedding = embed_document(text)
    except RuntimeError as exc:
        if _daemon_down(exc):
            return DAEMON_DOWN_REMEMBER
        raise
    import lib_chromadb
    return lib_chromadb.remember(text, embedding, timestamp)
