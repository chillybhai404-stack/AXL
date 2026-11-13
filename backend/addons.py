"""Add-on library: free, no-key tools an assistant can switch on with `use NAME`."""
import re
from urllib.parse import quote
import httpx
from . import mathx

H = {"User-Agent": "axu-core/1.0"}


def _get(url, **params):
    r = httpx.get(url, params=params or None, headers=H, timeout=8, follow_redirects=True)
    r.raise_for_status()
    return r.json()


# ---- math (offline) ----
def math_match(q, env):
    return mathx.find_math(q, env.keys()) is not None


def math_run(q, env):
    text = mathx.find_math(q, env.keys())
    kind, res = mathx.run_math(text, env)
    if kind == "solve":
        return res
    shown = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text))
    return f"{shown} = {res}"


# ---- weather ----
def _place(q):
    m = re.search(r"\b(?:in|at|for|of)\s+([a-z][a-z .'\-]{1,40}?)\s*(?:\?|$|today|tomorrow|now|right now|currently)", q.lower())
    return m[1].strip() if m else None


def weather_match(q, env):
    return bool(re.search(r"\b(weather|temperature|forecast)\b", q.lower())) and _place(q) is not None


def weather_run(q, env):
    g = _get("https://geocoding-api.open-meteo.com/v1/search", name=_place(q), count=1)
    if not g.get("results"):
        return None
    p = g["results"][0]
    w = _get("https://api.open-meteo.com/v1/forecast", latitude=p["latitude"], longitude=p["longitude"],
             current="temperature_2m,wind_speed_10m")["current"]
    return f"{p['name']}: {w['temperature_2m']}\u00b0C, wind {w['wind_speed_10m']} km/h"


# ---- dictionary ----
def _word(q):
    for pat in (r"\bdefine\s+(\w+)", r"\bdefinition of\s+(\w+)", r"\bmeaning of\s+(\w+)", r"\bwhat does\s+(\w+)\s+mean"):
        m = re.search(pat, q.lower())
        if m:
            return m[1]


def dictionary_match(q, env):
    return _word(q) is not None


def dictionary_run(q, env):
    w = _word(q)
    m = _get("https://api.dictionaryapi.dev/api/v2/entries/en/" + quote(w))[0]["meanings"][0]
    return f"{w} ({m['partOfSpeech']}): {m['definitions'][0]['definition']}"


# ---- countries ----
def _country(q):
    m = re.search(r"\b(?:of|in)\s+([a-z][a-z .'\-]{1,40}?)\s*(?:\?|$)", q.lower())
    return m[1].strip() if m else None


def countries_match(q, env):
    return bool(re.search(r"\b(capital|population|currency|languages?)\b", q.lower())) and _country(q) is not None


def countries_run(q, env):
    j = _get("https://restcountries.com/v3.1/name/" + quote(_country(q)),
             fields="name,capital,population,currencies,languages,region")
    d = j[0]
    name, ql = d["name"]["common"], q.lower()
    if "capital" in ql:
        return f"The capital of {name} is {', '.join(d['capital'])}."
    if "population" in ql:
        return f"{name} has about {d['population']:,} people."
    if "currency" in ql:
        return f"{name} uses " + ", ".join(f"{c['name']} ({c.get('symbol', '')})" for c in d["currencies"].values()) + "."
    return f"{name} speaks " + ", ".join(d["languages"].values()) + "."


# ---- currency ----
CUR = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]{3})\s*(?:to|in|into)\s*([a-z]{3})\b", re.I)


def currency_match(q, env):
    return CUR.search(q) is not None


def currency_run(q, env):
    a, f, t = CUR.search(q).groups()
    j = _get("https://api.frankfurter.app/latest", amount=a, **{"from": f.upper(), "to": t.upper()})
    return f"{a} {f.upper()} = {j['rates'][t.upper()]} {t.upper()}"


# ---- books ----
def books_match(q, env):
    return bool(re.search(r"\b(book|books|novel|author|wrote|written by)\b", q.lower()))


def books_run(q, env):
    s = re.sub(r"\b(book|books|novel|author|who|wrote|written|by|the|about|a|an|of|is|was|find|me|tell|what|which)\b", " ", q.lower())
    s = " ".join(s.split())
    if len(s) < 2:
        return None
    docs = _get("https://openlibrary.org/search.json", q=s, limit=1, fields="title,author_name,first_publish_year").get("docs")
    if not docs:
        return None
    d = docs[0]
    year = f" ({d['first_publish_year']})" if d.get("first_publish_year") else ""
    return f"\"{d['title']}\" by {', '.join(d.get('author_name', ['unknown'])[:2])}{year}"


# ---- joke ----
def joke_match(q, env):
    return "joke" in q.lower()


def joke_run(q, env):
    j = _get("https://official-joke-api.appspot.com/random_joke")
    return f"{j['setup']} {j['punchline']}"


# ---- wikipedia ----
def wikipedia_match(q, env):
    return True


LEAD = re.compile(r"^(?:please|hey|hi|hello|can you|could you|tell me|tell|do you know|i want to know|explain|describe|search|find|"
                  r"about|who|what|whom|is|are|was|were|an?|the|info on|information on)\b\s*", re.I)


def _topic(q):
    """'Who is Elon Musk?' -> 'Elon Musk'"""
    s, prev = q.strip().rstrip("?!. "), None
    while s and s != prev:
        prev, s = s, LEAD.sub("", s, count=1)
    return s.strip() or q.strip()


def _short(text, limit=420):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 120 else cut.rstrip() + "..."


def wikipedia_run(q, env):
    hits = _get("https://en.wikipedia.org/w/api.php", action="query", list="search", srsearch=_topic(q),
                srlimit=3, format="json")["query"]["search"]
    for h in hits:
        s = _get("https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(h["title"].replace(" ", "_"), safe=""))
        if s.get("type") != "disambiguation" and s.get("extract"):
            return _short(s["extract"])
    return None


CATALOG = {
    "math": {"title": "Math", "desc": "Solves sums and equations, offline.", "example": "What is (x * 15 = 10)?",
             "offline": True, "match": math_match, "run": math_run},
    "weather": {"title": "Weather", "desc": "Current weather for any city (Open-Meteo).", "example": "Weather in Paris?",
                "offline": False, "match": weather_match, "run": weather_run},
    "dictionary": {"title": "Dictionary", "desc": "Word definitions (Free Dictionary API).", "example": "Define serendipity",
                   "offline": False, "match": dictionary_match, "run": dictionary_run},
    "countries": {"title": "Countries", "desc": "Capital, population, currency, languages.", "example": "Capital of Japan?",
                  "offline": False, "match": countries_match, "run": countries_run},
    "currency": {"title": "Currency", "desc": "Live exchange rates (Frankfurter).", "example": "Convert 100 USD to INR",
                 "offline": False, "match": currency_match, "run": currency_run},
    "books": {"title": "Books", "desc": "Find books and authors (Open Library).", "example": "Who wrote Dune?",
              "offline": False, "match": books_match, "run": books_run},
    "joke": {"title": "Jokes", "desc": "A random joke.", "example": "Tell me a joke",
             "offline": False, "match": joke_match, "run": joke_run},
    "wikipedia": {"title": "Wikipedia", "desc": "Answers general questions. Always runs last.", "example": "Who was Ada Lovelace?",
                  "offline": False, "match": wikipedia_match, "run": wikipedia_run},
}


def listing():
    return [{"name": k, **{f: v[f] for f in ("title", "desc", "example", "offline")}} for k, v in CATALOG.items()]
