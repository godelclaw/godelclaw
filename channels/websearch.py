#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class SearchFailure(RuntimeError):
    def __init__(self, provider, reason, attempts=()):
        super().__init__(reason)
        self.provider = provider
        self.reason = reason
        self.attempts = list(attempts)


def _failure(provider, error):
    # Provider exception messages can contain request URLs or credentials.
    if isinstance(error, SearchFailure):
        return dict(provider=error.provider, reason=error.reason,
                    **({"attempts": error.attempts} if error.attempts else {}))
    if isinstance(error, urllib.error.HTTPError):
        reason = "http-%d" % error.code
    elif isinstance(error, TimeoutError):
        reason = "timeout"
    elif isinstance(error, urllib.error.URLError):
        reason = "timeout" if isinstance(error.reason, TimeoutError) else "network-error"
    elif isinstance(error, (json.JSONDecodeError, UnicodeError, TypeError, AttributeError)):
        reason = "invalid-response"
    else:
        reason = "search-failed"
    return dict(provider=provider, reason=reason)


def _error_result(provider, error):
    return "SEARCH_ERROR: " + json.dumps(_failure(provider, error), sort_keys=True)

class DDGParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.in_snippet = False
        self.current_title = None
        self.current_url = None
        self.current_snippet = None
        self.results = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(str(attrs.get("class", "")).split())
        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self.in_title = True
            self.current_title = ""
            self.current_url = self._decode_ddg_url(attrs.get("href", ""))
        elif (
            ("result__snippet" in classes and tag in ("a", "td"))
            or ("result-snippet" in classes and tag == "td")
        ):
            self.in_snippet = True
            self.current_snippet = ""

    def handle_endtag(self, tag):
        if tag == "a":
            self.in_title = False
            if self.in_snippet:
                self._finish_result()
                self.in_snippet = False
        elif tag == "td" and self.in_snippet:
            self._finish_result()
            self.in_snippet = False

    def handle_data(self, data):
        if self.in_title:
            self.current_title += data
        elif self.in_snippet:
            self.current_snippet += data

    def _decode_ddg_url(self, href):
        href = str(href or "")
        if href.startswith("//"):
            href = "https:" + href
        try:
            parsed = urllib.parse.urlparse(href)
            query = urllib.parse.parse_qs(parsed.query)
            if "uddg" in query and query["uddg"]:
                return query["uddg"][0]
        except Exception:
            pass
        return href

    def _finish_result(self):
        if self.current_title and self.current_snippet:
            self.results.append({
                "title": self.current_title.strip(),
                "url": self.current_url or "",
                "snippet": self.current_snippet.strip(),
            })
        self.current_title = None
        self.current_url = None
        self.current_snippet = None

def _clean(value):
    return " ".join(str(value or "").split())

def _tavily_base_url():
    return os.environ.get("TAVILY_BASE_URL", "https://api.tavily.com").rstrip("/")

def tavily_search(query, max_results=10):
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise SearchFailure("tavily", "missing-api-key")

    count = max(1, min(20, int(max_results or 10)))
    payload = {
        "query": query,
        "max_results": count,
    }
    if os.environ.get("TAVILY_INCLUDE_ANSWER", "").lower() in ("1", "true", "yes"):
        payload["include_answer"] = True
    if os.environ.get("TAVILY_SEARCH_DEPTH"):
        payload["search_depth"] = os.environ["TAVILY_SEARCH_DEPTH"]
    if os.environ.get("TAVILY_TOPIC"):
        payload["topic"] = os.environ["TAVILY_TOPIC"]

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _tavily_base_url() + "/search",
        data=data,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": "pettaclaw-websearch/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read().decode("utf-8"))

    results = []
    for item in body.get("results", [])[:count]:
        results.append({
            "title": _clean(item.get("title")),
            "url": _clean(item.get("url")),
            "snippet": _clean(item.get("content") or item.get("snippet")),
        })
    if body.get("answer"):
        results.insert(0, {
            "title": "Tavily answer",
            "url": "",
            "snippet": _clean(body.get("answer")),
        })
    return results[:count]

def ddg_search(query, max_results=10):
    results = []
    failures = []
    completed = False
    for base in (
        "https://lite.duckduckgo.com/lite/?q=",
        "https://html.duckduckgo.com/html/?q=",
    ):
        url = base + urllib.parse.quote_plus(query)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                html = r.read().decode("utf-8", errors="ignore")
            parser = DDGParser()
            parser.feed(html)
            if not parser.results and any(marker in html.lower() for marker in
                    ("anomaly-modal", "challenge-form", "bots use duckduckgo too")):
                raise SearchFailure("ddg", "challenge-required")
            results = parser.results[:max_results]
            completed = True
        except Exception as error:
            failures.append(_failure("ddg", error))
            continue
        if results:
            break
    if not completed:
        raise SearchFailure("ddg", "all-endpoints-failed", failures)
    return [
        {
            "title": _clean(r.get("title")),
            "url": _clean(r.get("url")),
            "snippet": _clean(r.get("snippet")),
        }
        for r in results
    ]

def _provider_from_query(query):
    text = str(query or "").strip()
    lower = text.lower()
    for prefix, provider in (
        ("tavily:", "tavily"),
        ("ddg:", "ddg"),
        ("duckduckgo:", "ddg"),
        ("auto:", "auto"),
    ):
        if lower.startswith(prefix):
            return provider, text[len(prefix):].strip()
    return os.environ.get("METTACLAW_SEARCH_PROVIDER", "auto").strip().lower(), text

def search_(query, max_results=10, *, _attempts=None):
    provider, query = _provider_from_query(query)
    if provider in ("tavily", "api"):
        return tavily_search(query, max_results)
    if provider in ("ddg", "duckduckgo", "html"):
        return ddg_search(query, max_results)
    if provider != "auto":
        raise SearchFailure("configured", "unknown-provider")

    failures = []
    try:
        results = tavily_search(query, max_results)
        if results:
            return results
    except Exception as error:
        failures.append(_failure("tavily", error))
    if _attempts is not None:
        _attempts.extend(failures)
    try:
        return ddg_search(query, max_results)
    except Exception as error:
        raise SearchFailure("auto", "all-providers-failed",
                            failures + [_failure("ddg", error)]) from None

def _format_results(results):
    ret = "("
    for r in results:
        url = (" URL: " + r["url"]) if r.get("url") else ""
        ret += "(TITLE: " + r["title"] + url + " SNIPPET: " + r["snippet"] + ") "
    ret += ")"
    return ret

def search(query, max_results=10):
    try:
        attempts = []
        result = _format_results(search_(query, max_results, _attempts=attempts))
        if attempts:
            result = "SEARCH_FALLBACK: " + json.dumps(attempts, sort_keys=True) + "\n" + result
        return result
    except Exception as error:
        return _error_result("configured", error)

def search_tavily(query, max_results=10):
    try:
        return _format_results(tavily_search(query, max_results))
    except Exception as error:
        return _error_result("tavily", error)

def search_ddg(query, max_results=10):
    try:
        return _format_results(ddg_search(query, max_results))
    except Exception as error:
        return _error_result("ddg", error)
