"""AXL engine: multi-file parser + runtime. No LLM needed.

  assistant: Name
  import library                      # pull in library.axl (tables, lets, rules)
  let tax = 0.18                      # constants and math
  table menu (name, price)            # inline data
  row "Naan", 40
  use math                            # add-ons: math weather dictionary countries currency books joke wikipedia
  when asked about "price" -> find in menu where price < 100 -> "{name}: Rs {price * (1 + tax):2}"
  when asked about "cheap" -> sql "SELECT ..." -> "{name}"
  when asked about "refund" -> search policies -> "{text}"
  when asked about anything -> math
  if unsure -> say "I don't know"
  fallback llm vault:NAME model MODEL
  fallback transformer model MODEL_NAME
"""
import re, sqlite3
from . import store, mathx, addons, llm, ai_lab

STOP = set(
    "the a an is are was were be do does did can could you your yours i me my we our to of in on at for and or "
    "with this that it its have has from about which when where who whom what how tell please give show there any some".split()
)


def stem(w):
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def words(s):
    return {stem(w) for w in re.findall(r"\w+", s.lower()) if w not in STOP and len(w) > 1}


# ---------- parsing ----------
def strip_comment(line):
    q = None
    for i, c in enumerate(line):
        if q:
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c == "#":
            return line[:i]
    return line


def split_commas(s):
    out, cur, depth, q = [], "", 0, None
    for c in s:
        if q:
            cur += c
            if c == q:
                q = None
        elif c in "\"'":
            q = c
            cur += c
        elif c in "([":
            depth += 1
            cur += c
        elif c in ")]":
            depth -= 1
            cur += c
        elif c == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
    out.append(cur.strip())
    return out


def _q(s):
    m = re.fullmatch(r'\s*"(.*)"\s*', s)
    if not m:
        raise ValueError("Put the text in double quotes")
    return m[1]


def _need(parts, i):
    if len(parts) <= i:
        raise ValueError('Add the answer in quotes after the second ->, like: -> "{name} costs {price}"')
    return _q(parts[i])


def _rule(spec, rest):
    parts = rest.split(" -> ")
    if len(parts) < 2:
        raise ValueError('Use: when asked about "word" -> action')
    head = parts[0].strip()
    kws = ["*"] if head.lower() == "anything" else [k.lower() for k in re.findall(r'"([^"]+)"', head)]
    if not kws:
        raise ValueError('Put trigger words in quotes, like: when asked about "price", "cost"')
    a = parts[1].strip()
    r = {"kws": kws, "cond": None, "tpl": None}
    if a.lower().startswith("say "):
        r.update(act="say", arg=_q(a[4:]))
    elif a.lower() == "math":
        r.update(act="math", arg=None, tpl=_q(parts[2]) if len(parts) > 2 else None)
    elif m := re.fullmatch(r"find in (\w+)(?:\s+where\s+(.+))?", a, re.I):
        r.update(act="find", arg=m[1], cond=mathx.parse_expr(m[2]) if m[2] else None, tpl=_need(parts, 2))
    elif m := re.fullmatch(r"search (\w+)", a, re.I):
        r.update(act="search", arg=m[1], tpl=_need(parts, 2))
    elif a.lower().startswith("sql "):
        sql = _q(a[4:])
        if not sql.lstrip().lower().startswith("select"):
            raise ValueError("Only SELECT queries are allowed")
        r.update(act="sql", arg=sql, tpl=_need(parts, 2))
    else:
        raise ValueError("Unknown action. Use: say, math, find in, search, sql")
    spec["rules"].append(r)


def _stmt(spec, files, load, line, table):
    low = line.lower()
    if low.startswith("row "):
        if not table:
            raise ValueError("'row' must come right after a 'table' line")
        t = spec["tables"][table]
        vals = [mathx.ev(mathx.parse_expr(p), spec["lets"]) for p in split_commas(line[4:])]
        if len(vals) != len(t["cols"]):
            raise ValueError(f"This row has {len(vals)} values but table '{table}' has {len(t['cols'])} columns")
        t["rows"].append(vals)
        return table
    m = re.fullmatch(r"table\s+(\w+)\s*\((.*)\)", line, re.I)
    if m:
        cols = [c.strip() for c in m[2].split(",") if c.strip()]
        if not cols or not all(re.fullmatch(r"[A-Za-z_]\w*", c) for c in cols):
            raise ValueError("Column names must be plain words, like: table menu (name, price)")
        spec["tables"][m[1]] = {"cols": cols, "rows": []}
        return m[1]
    if low.startswith("assistant:"):
        spec["name"] = line.split(":", 1)[1].strip() or "Assistant"
    elif low.startswith("instructions "):
        spec["instructions"] = _q(line[13:])
    elif low.startswith("import "):
        name = line[7:].strip()
        fname = name if name.endswith(".axl") else name + ".axl"
        if fname not in files:
            raise ValueError(f"No file named {fname}. Create it in the Files list.")
        load(fname)
    elif m := re.fullmatch(r"let\s+([A-Za-z_]\w*)\s*=\s*(.+)", line, re.I):
        spec["lets"][m[1]] = mathx.ev(mathx.parse_expr(m[2]), spec["lets"])
    elif m := re.fullmatch(r"use\s+(\w+)", line, re.I):
        name = m[1].lower()
        if name not in addons.CATALOG:
            raise ValueError(f"Unknown add-on '{name}'. Available: " + ", ".join(addons.CATALOG))
        if name not in spec["addons"]:
            spec["addons"].append(name)
    elif low == "fallback wikipedia":
        if "wikipedia" not in spec["addons"]:
            spec["addons"].append("wikipedia")
    elif m := re.fullmatch(r"fallback llm vault:(\w+)(?:\s+model\s+(\S+))?", line, re.I):
        spec["fallbacks"].append(("vault", m[1], m[2]))
    elif m := re.fullmatch(r"fallback transformer model ([A-Za-z0-9_-]+)", line, re.I):
        spec["fallbacks"].append(("transformer", m[1], None))
    elif low.startswith("if unsure -> say "):
        spec["unsure"] = _q(line[17:])
    elif low.startswith("when asked about "):
        _rule(spec, line[17:])
    else:
        raise ValueError("Don't understand this line. Lines start with: assistant, import, let, table, row, use, when, if unsure, fallback")
    return None


def parse_project(files, main="main.axl"):
    spec = {"name": "Assistant", "instructions": "", "unsure": "I don't know.", "rules": [], "addons": [],
            "fallbacks": [], "lets": {}, "tables": {}, "errors": []}
    seen = set()

    def load(fname):
        if fname in seen:
            return
        seen.add(fname)
        table = None
        for n, raw in enumerate(files[fname].splitlines(), 1):
            line = strip_comment(raw).strip()
            if not line:
                continue
            try:
                table = _stmt(spec, files, load, line, table)
            except NameError as e:
                spec["errors"].append({"file": fname, "line": n, "message": f"Unknown name '{e}'. Define it first with: let {e} = ..."})
                table = None
            except Exception as e:
                spec["errors"].append({"file": fname, "line": n, "message": str(e) or "Can't understand this line"})
                table = None

    if main not in files:
        spec["errors"].append({"file": main, "line": 1, "message": "main.axl is missing"})
    else:
        load(main)
    return spec


def validate(files):
    return parse_project(files)["errors"]


# ---------- running ----------
def coerce(v):
    if isinstance(v, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", v.strip()):
        return float(v) if "." in v else int(v)
    return v


def render(tpl, env):
    def one(m):
        inner, dec = m[1].strip(), None
        dm = re.fullmatch(r"(.+?)\s*:\s*(\d)", inner)
        if dm:
            inner, dec = dm[1], int(dm[2])
        try:
            if re.fullmatch(r"[A-Za-z_]\w*", inner) and inner in env:
                v = env[inner]
            else:
                v = mathx.ev(mathx.parse_expr(inner), env)
            if dec is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
                return mathx.fmt(v, dec)
            return mathx.fmt(v)
        except Exception:
            return "?"

    return re.sub(r"\{([^{}]+)\}", one, tpl)


def _row_env(lets, row):
    return {**lets, **{k: coerce(row[k]) for k in row.keys()}}


def _search(con, table, qw):
    terms = sorted(qw)
    if not terms:
        return None
    try:
        match = " OR ".join('"%s"' % t.replace('"', "") for t in terms)
        row = con.execute(f'SELECT text FROM "{table}" WHERE "{table}" MATCH ? ORDER BY rank LIMIT 1', (match,)).fetchone()
    except sqlite3.OperationalError:
        like = " OR ".join(["text LIKE ?"] * len(terms))
        row = con.execute(f'SELECT text FROM "{table}" WHERE {like} LIMIT 1', [f"%{t}%" for t in terms]).fetchone()
    return row["text"] if row else None


def _run(con, spec, r, qw, q):
    act, arg, tpl, lets = r["act"], r["arg"], r["tpl"], spec["lets"]
    if act == "say":
        return render(arg, lets)
    if act == "math":
        text = mathx.find_math(q, lets.keys())
        if not text:
            return None
        kind, res = mathx.run_math(text, lets)
        shown = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text))
        if tpl:
            return render(tpl, {**lets, "result": res, "expr": shown})
        return res if kind == "solve" else f"{shown} = {res}"
    if act == "sql":
        found = con.execute(arg).fetchmany(20)
        return "\n".join(render(tpl, _row_env(lets, x)) for x in found)
    if act == "find":
        mw = qw if r["kws"] == ["*"] else qw - words(" ".join(r["kws"]))
        best, score = None, 0
        for row in con.execute(f'SELECT * FROM "{arg}"'):
            env = _row_env(lets, row)
            if r["cond"] is not None:
                try:
                    if not mathx._truth(mathx.ev(r["cond"], env)):
                        continue
                except Exception:
                    continue
            s = len(mw & words(" ".join(str(v) for v in row)))
            if s > score:
                best, score = env, s
        return render(tpl, best) if best else None
    if act == "search":
        text = _search(con, arg, qw)
        return render(tpl, {**lets, "text": text}) if text else None


def _kw_hit(k, ql):
    suffix = r"(?:s|es)?" if len(k) >= 4 else ""
    return re.search(r"(?<!\w)" + re.escape(k) + suffix + r"(?!\w)", ql) is not None


def _load_inline(con, spec):
    for name, t in spec["tables"].items():
        con.execute(f'CREATE TEMP TABLE "{name}" ({", ".join(chr(34) + c + chr(34) for c in t["cols"])})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(t["cols"]))})', t["rows"])
    con.commit()


def load_files(aid):
    return {f["name"]: f["content"] for f in store.sources(aid)}


def ask(aid, q, debug=False):
    spec = parse_project(load_files(aid))
    if spec["errors"]:
        e = spec["errors"][0]
        return {"answer": f"This assistant has a rules error ({e['file']} line {e['line']}): {e['message']}", "source": "error"}
    con = store.data_db(aid)
    try:
        _load_inline(con, spec)
        con.execute("PRAGMA query_only=ON")
        ql, qw = q.lower(), words(q)
        for r in spec["rules"]:
            if r["kws"] != ["*"] and not any(_kw_hit(k, ql) for k in r["kws"]):
                continue
            try:
                out = _run(con, spec, r, qw, q)
            except Exception:
                out = None
            if out:
                return {"answer": out, "source": "rules"}
    finally:
        con.close()
    errs = []
    for name in sorted(spec["addons"], key=lambda n: {"math": 0, "wikipedia": 2}.get(n, 1)):
        a = addons.CATALOG[name]
        try:
            if a["match"](q, spec["lets"]):
                out = a["run"](q, spec["lets"])
                if out:
                    return {"answer": out, "source": name}
                errs.append(f"{name}: nothing found")
        except Exception as ex:
            errs.append(f"{name}: {type(ex).__name__} {str(ex)[:80]}")
    for kind, name, model in spec["fallbacks"]:
        try:
            if kind == "transformer":
                prompt = f"User: {q}\nAssistant:"
                generated = ai_lab.generate_text(name, prompt, max_new_tokens=180, temperature=0.7)
                out = generated["continuation"].strip()
                source = "local-transformer"
            else:
                entry = store.vault_entry(name)
                if model:
                    entry["model"] = model
                out = llm.call(entry, [{"role": "system", "content": spec["instructions"] or "Answer briefly."},
                                       {"role": "user", "content": q}], 300)
                source = "llm"
        except Exception as ex:
            out = None
            errs.append(f"{kind} {name}: {type(ex).__name__}")
        if out:
            return {"answer": out, "source": source}
    res = {"answer": spec["unsure"], "source": "unsure"}
    if debug and errs:
        res["debug"] = "; ".join(errs)
    return res
