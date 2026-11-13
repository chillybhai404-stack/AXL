"""Calls to AI providers (OpenAI-compatible, Anthropic, Gemini) with failover/rotation."""
import collections
import httpx

_rot = collections.defaultdict(int)


def call(e, messages, max_tokens=500):
    """e: dict(provider, secret, base_url, model). Returns the reply text."""
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    chat = [m for m in messages if m["role"] != "system"]
    p = e.get("provider") or "openai"
    if p == "anthropic":
        body = {"model": e["model"] or "claude-haiku-5-5", "max_tokens": max_tokens, "messages": chat}
        if system:
            body["system"] = system
        r = httpx.post((e["base_url"] or "https://api.anthropic.com").rstrip("/") + "/v1/messages",
                       headers={"x-api-key": e["secret"], "anthropic-version": "2023-06-01"}, json=body, timeout=60)
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    if p == "gemini":
        base = (e["base_url"] or "https://generativelanguage.googleapis.com").rstrip("/")
        body = {"contents": [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in chat],
                "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        r = httpx.post(f"{base}/v1beta/models/{e['model'] or 'gemini-2.0-flash'}:generateContent",
                       headers={"x-goog-api-key": e["secret"]}, json=body, timeout=60)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    r = httpx.post((e["base_url"] or "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
                   headers={"Authorization": f"Bearer {e['secret']}"},
                   json={"model": e["model"] or "gpt-4o-mini", "max_tokens": max_tokens, "messages": messages}, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def route(key_id, entries, messages, mode="failover", max_tokens=500):
    order = list(entries)
    if mode == "rotate" and order:
        k = _rot[key_id] % len(order)
        _rot[key_id] += 1
        order = order[k:] + order[:k]
    failed = []
    for e in order:
        try:
            return {"answer": call(e, messages, max_tokens), "provider": e["name"]}
        except Exception:
            failed.append(e["name"])
    raise RuntimeError("All merged providers failed: " + ", ".join(failed))
