"""Provider adapters for OpenAI-compatible APIs, Anthropic and Gemini."""
import collections
import httpx
from urllib.parse import urlsplit

_rot = collections.defaultdict(int)


def _clean_base(value: str, fallback: str) -> str:
    value = (value or fallback).strip().rstrip("/")
    if not value:
        value = fallback
    # Reject malformed/non-http URLs early with a useful error.
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Provider Base URL must begin with https:// (or http:// for local testing)")
    return value


def _openai_chat_url(base: str) -> str:
    """Accept an API root, /v1 root, or a full chat/completions URL."""
    base = _clean_base(base, "https://api.openai.com/v1")
    low = base.lower().rstrip("/")
    if low.endswith("/chat/completions"):
        return base
    if low.endswith("/v1") or low.endswith("/openai/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        detail = (response.text or "").strip().replace("\n", " ")[:320]
        return f"HTTP {response.status_code}" + (f": {detail}" if detail else "")
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return f"Connection failed: {type(exc).__name__}. Check the provider URL, internet access and server network rules."
    if isinstance(exc, httpx.InvalidURL):
        return "Invalid provider URL. Check the Base URL field."
    return f"{type(exc).__name__}: {str(exc)[:240]}"


def call(e, messages, max_tokens=500):
    """e: dict(provider, secret, base_url, model). Returns reply text."""
    system = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    chat = [{"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
            for m in messages if m.get("role") != "system"]
    if not chat:
        chat = [{"role": "user", "content": "Hello"}]
    p = (e.get("provider") or "openai").lower()
    secret = (e.get("secret") or "").strip()
    if not secret:
        raise ValueError("Provider API key is empty")

    timeout = httpx.Timeout(45.0, connect=12.0)
    if p == "anthropic":
        base = _clean_base(e.get("base_url"), "https://api.anthropic.com")
        body = {"model": e.get("model") or "claude-haiku-4-5-20251001", "max_tokens": max_tokens, "messages": chat}
        if system:
            body["system"] = system
        if base.endswith("/v1/messages"):
            url = base
        elif base.endswith("/v1"):
            url = base + "/messages"
        else:
            url = base + "/v1/messages"
        r = httpx.post(url, headers={"x-api-key": secret, "anthropic-version": "2023-06-01"}, json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        parts = data.get("content") or []
        answer = "".join(x.get("text", "") for x in parts if x.get("type") == "text")
        if not answer:
            raise ValueError("Anthropic returned no text content")
        return answer

    if p == "gemini":
        base = _clean_base(e.get("base_url"), "https://generativelanguage.googleapis.com")
        model = e.get("model") or "gemini-3.8-flash"
        if "/models/" in base and base.endswith(":generateContent"):
            url = base
        else:
            base = base[:-7] if base.endswith("/v1beta") else base
            url = f"{base}/v1beta/models/{model}:generateContent"
        body = {"contents": [{"role": "user" if m["role"] in ("user", "system") else "model",
                              "parts": [{"text": m["content"]}]} for m in chat],
                "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        r = httpx.post(url, headers={"x-goog-api-key": secret}, json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        candidates = data.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        answer = "".join(x.get("text", "") for x in parts)
        if not answer:
            raise ValueError("Gemini returned no text. The prompt may have been blocked or the model unavailable.")
        return answer

    url = _openai_chat_url(e.get("base_url"))
    body = {"model": e.get("model") or "gpt-4o-mini", "max_tokens": max_tokens, "messages": messages}
    r = httpx.post(url, headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}, json=body, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    choices = data.get("choices") or []
    answer = choices[0].get("message", {}).get("content") if choices else None
    if isinstance(answer, list):
        answer = "".join(part.get("text", "") for part in answer if isinstance(part, dict))
    if not answer:
        raise ValueError("Provider returned no assistant text")
    return str(answer)


def test_connection(entry):
    """Small real provider test; returns the provider reply or a safe diagnostic."""
    try:
        answer = call(entry, [{"role": "user", "content": "Reply with exactly: AXL connection successful"}], 40)
        return {"ok": True, "provider": entry.get("provider", "openai"), "model": entry.get("model") or "provider default", "answer": answer}
    except Exception as exc:
        return {"ok": False, "provider": entry.get("provider", "openai"), "error": _error_message(exc)}


def route(key_id, entries, messages, mode="failover", max_tokens=500):
    order = list(entries)
    if mode == "rotate" and order:
        k = _rot[key_id] % len(order)
        _rot[key_id] += 1
        order = order[k:] + order[:k]
    if not order:
        raise RuntimeError("No active provider keys are attached to this merged key")
    failed = []
    for e in order:
        try:
            return {"answer": call(e, messages, max_tokens), "provider": e["name"]}
        except Exception as exc:
            failed.append(f"{e.get('name', 'provider')}: {_error_message(exc)}")
    raise RuntimeError("All merged providers failed. " + " | ".join(failed))
