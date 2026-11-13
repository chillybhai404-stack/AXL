"""Safe math for AXL: expressions, relations and equation solving. No eval()."""
import math, re

KEYWORDS = {"and", "or", "not"}
CONSTS = {"pi": math.pi, "e": math.e, "true": True, "false": False}
TOK = re.compile(r"""\s*(?:(\d+(?:\.\d+)?)|("[^"]*"|'[^']*')|([A-Za-z_]\w*)|(\*\*|==|!=|>=|<=|[-+*/^%()<>=,]))""")


def tokenize(s):
    s = s.strip()
    out, pos = [], 0
    while pos < len(s):
        m = TOK.match(s, pos)
        if not m:
            raise ValueError(f"Unexpected character '{s[pos:].strip()[:1]}'")
        pos = m.end()
        if m[1] is not None:
            out.append(("num", float(m[1]) if "." in m[1] else int(m[1])))
        elif m[2] is not None:
            out.append(("str", m[2][1:-1]))
        elif m[3] is not None:
            out.append(("id", m[3]))
        else:
            out.append(("op", "^" if m[4] == "**" else m[4]))
    return out


class _P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def is_op(self, *ops):
        k, v = self.peek()
        return k == "op" and v in ops

    def is_id(self, *names):
        k, v = self.peek()
        return k == "id" and v.lower() in names

    def expect(self, op):
        if not self.is_op(op):
            raise ValueError(f"Expected '{op}'")
        self.i += 1

    def parse(self):
        if not self.t:
            raise ValueError("Empty expression")
        n = self.or_()
        if self.i < len(self.t):
            raise ValueError(f"Unexpected '{self.peek()[1]}'")
        return n

    def or_(self):
        n = self.and_()
        while self.is_id("or"):
            self.i += 1
            n = ("or", n, self.and_())
        return n

    def and_(self):
        n = self.not_()
        while self.is_id("and"):
            self.i += 1
            n = ("and", n, self.not_())
        return n

    def not_(self):
        if self.is_id("not"):
            self.i += 1
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        n = self.add()
        if self.is_op("==", "=", "!=", "<", ">", "<=", ">="):
            op = self.next()[1]
            n = ("cmp", "==" if op == "=" else op, n, self.add())
        return n

    def add(self):
        n = self.mul()
        while self.is_op("+", "-"):
            op = self.next()[1]
            n = ("bin", op, n, self.mul())
        return n

    def mul(self):
        n = self.unary()
        while True:
            if self.is_op("*", "/", "%"):
                op = self.next()[1]
                n = ("bin", op, n, self.unary())
            elif n[0] == "num" and ((self.peek()[0] == "id" and self.peek()[1].lower() not in KEYWORDS) or self.is_op("(")):
                n = ("bin", "*", n, self.unary())  # implicit multiply: 2x, 3(x+1)
            else:
                return n

    def unary(self):
        if self.is_op("-"):
            self.i += 1
            return ("neg", self.unary())
        if self.is_op("+"):
            self.i += 1
            return self.unary()
        return self.pow()

    def pow(self):
        n = self.atom()
        if self.is_op("^"):
            self.i += 1
            return ("bin", "^", n, self.unary())
        return n

    def atom(self):
        k, v = self.next()
        if k in ("num", "str"):
            return (k, v)
        if k == "id":
            if v.lower() in KEYWORDS:
                raise ValueError(f"Unexpected '{v}'")
            if self.is_op("("):
                self.i += 1
                args = []
                if not self.is_op(")"):
                    args.append(self.or_())
                    while self.is_op(","):
                        self.i += 1
                        args.append(self.or_())
                self.expect(")")
                return ("call", v.lower(), args)
            return ("var", v)
        if k == "op" and v == "(":
            n = self.or_()
            self.expect(")")
            return n
        raise ValueError("Incomplete expression" if k is None else f"Unexpected '{v}'")


def parse_expr(s):
    return _P(tokenize(s)).parse()


def _num(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            pass
    raise ValueError(f"'{v}' is not a number")


def _truth(v):
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no")
    return bool(v)


def _eq(a, b):
    if isinstance(a, str) or isinstance(b, str):
        return str(a).strip().lower() == str(b).strip().lower()
    a, b = _num(a), _num(b)
    return abs(a - b) <= 1e-9 * max(1, abs(a), abs(b))


def _sqrt(x):
    return math.sqrt(_num(x))


FUNCS = {
    "sqrt": _sqrt,
    "abs": lambda x: abs(_num(x)),
    "round": lambda x, d=0: round(_num(x)) if int(d) == 0 else round(_num(x), int(d)),
    "floor": lambda x: math.floor(_num(x)),
    "ceil": lambda x: math.ceil(_num(x)),
    "min": lambda *a: min(_num(x) for x in a),
    "max": lambda *a: max(_num(x) for x in a),
    "len": lambda s: len(str(s)),
    "upper": lambda s: str(s).upper(),
    "lower": lambda s: str(s).lower(),
    "sin": lambda x: math.sin(_num(x)),
    "cos": lambda x: math.cos(_num(x)),
    "tan": lambda x: math.tan(_num(x)),
    "log": lambda x: math.log(_num(x)),
    "log10": lambda x: math.log10(_num(x)),
}


def ev(n, env):
    k = n[0]
    if k in ("num", "str"):
        return n[1]
    if k == "var":
        if n[1] in env:
            return env[n[1]]
        if n[1].lower() in CONSTS:
            return CONSTS[n[1].lower()]
        raise NameError(n[1])
    if k == "neg":
        return -_num(ev(n[1], env))
    if k == "not":
        return not _truth(ev(n[1], env))
    if k == "or":
        return _truth(ev(n[1], env)) or _truth(ev(n[2], env))
    if k == "and":
        return _truth(ev(n[1], env)) and _truth(ev(n[2], env))
    if k == "cmp":
        a, b, op = ev(n[2], env), ev(n[3], env), n[1]
        if op == "==":
            return _eq(a, b)
        if op == "!=":
            return not _eq(a, b)
        a, b = (a.lower(), b.lower()) if isinstance(a, str) and isinstance(b, str) else (_num(a), _num(b))
        return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
    if k == "bin":
        op, a, b = n[1], ev(n[2], env), ev(n[3], env)
        if op == "+" and (isinstance(a, str) or isinstance(b, str)):
            return fmt(a) + fmt(b)
        a, b = _num(a), _num(b)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b
        if op == "%":
            return a % b
        if abs(b) > 1000:
            raise ValueError("Exponent too large")
        r = a ** b
        if isinstance(r, complex):
            raise ValueError("No real result")
        return r
    if k == "call":
        name, args = n[1], n[2]
        if name == "if":
            if len(args) != 3:
                raise ValueError("if(condition, a, b) needs 3 values")
            return ev(args[1], env) if _truth(ev(args[0], env)) else ev(args[2], env)
        f = FUNCS.get(name)
        if not f:
            raise ValueError(f"Unknown function {name}()")
        return f(*[ev(a, env) for a in args])
    raise ValueError("Bad expression")


def free_vars(n):
    k = n[0]
    if k == "var":
        return set() if n[1].lower() in CONSTS else {n[1]}
    if k in ("num", "str"):
        return set()
    if k == "call":
        return set().union(*[free_vars(a) for a in n[2]]) if n[2] else set()
    return set().union(*[free_vars(c) for c in n[1:] if isinstance(c, tuple)])


def fmt(v, dec=None):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return str(v)
        if dec is not None:
            return f"{v:.{dec}f}"
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def solve(node, env):
    """node = ('cmp','==',L,R) with exactly one unknown. Returns (name, roots) - roots None means any value."""
    unknown = free_vars(node) - set(env)
    if len(unknown) != 1:
        raise ValueError("An equation needs exactly one unknown")
    (v,) = unknown
    L, R = node[2], node[3]

    def safe(x):
        try:
            e = {**env, v: x}
            y = _num(ev(L, e)) - _num(ev(R, e))
            return y if math.isfinite(y) else None
        except Exception:
            return None

    f0, f1, f2 = safe(0), safe(1), safe(2)
    if None not in (f0, f1, f2) and abs(f2 - 2 * f1 + f0) <= 1e-9 * (1 + abs(f0) + abs(f1) + abs(f2)):
        a = f1 - f0
        if abs(a) < 1e-12:
            return v, (None if abs(f0) <= 1e-9 else [])
        return v, [-f0 / a]
    roots, px, py, x = [], None, None, -1000.0
    while x <= 1000.0:
        y = safe(x)
        if y is None:
            px = py = None
        else:
            if abs(y) < 1e-9:
                roots.append(x)
            elif py is not None and py * y < 0:
                lo, hi, ylo = px, x, py
                for _ in range(80):
                    mid = (lo + hi) / 2
                    ym = safe(mid)
                    if ym is None:
                        break
                    if ylo * ym <= 0:
                        hi = mid
                    else:
                        lo, ylo = mid, ym
                roots.append((lo + hi) / 2)
            px, py = x, y
        x += 0.5
    out = []
    for r in sorted(roots):
        if not out or abs(r - out[-1]) > 1e-6:
            out.append(r)
    return v, out[:4]


def run_math(text, env=None):
    """Evaluate or solve. Returns ('solve'|'value', result_text). Raises ValueError on bad input."""
    env = env or {}
    ast = parse_expr(text)
    try:
        if ast[0] == "cmp" and ast[1] == "==" and (free_vars(ast) - set(env)):
            v, roots = solve(ast, env)
            if roots is None:
                return "solve", f"{v} can be any value"
            if not roots:
                return "solve", "No solution found"
            return "solve", " or ".join(f"{v} = {fmt(r)}" for r in roots)
        return "value", fmt(ev(ast, env))
    except NameError as e:
        raise ValueError(f"Unknown name {e}")
    except ZeroDivisionError:
        raise ValueError("Division by zero")
    except OverflowError:
        raise ValueError("Number too large")


OPS = set("+-*/^%()<>=") | {"**", "==", "!=", ">=", "<="}
ARITH = OPS - set("()")
VARS = set("xyznmk")
WORDS = [
    (r"\bmultiplied by\b|\btimes\b", " * "), (r"\bdivided by\b", " / "), (r"\bplus\b", " + "),
    (r"\bminus\b", " - "), (r"\bto the power of\b", " ^ "), (r"\bsquared\b", " ^ 2 "), (r"\bcubed\b", " ^ 3 "),
]


def find_math(q, known=()):
    """Pull a math expression or equation out of a sentence. Returns text or None."""
    s = q.lower().replace("\u00d7", "*").replace("\u00f7", "/")
    s = re.sub(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s*of\s*", r"(\1 / 100) * ", s)
    for pat, rep in WORDS:
        s = re.sub(pat, rep, s)
    toks = re.findall(r"\d+(?:\.\d+)?|[a-z_]\w*|\*\*|==|!=|>=|<=|[-+*/^%()<>=]", s)
    known = set(known)

    def ok(t):
        return t[0].isdigit() or t in OPS or t in VARS or t in FUNCS or t in known or t in ("pi",)

    best, run = None, []
    for t in toks + [None]:
        if t is not None and ok(t):
            run.append(t)
            continue
        r = run
        run = []
        while r and r[0] in ARITH and r[0] != "-":
            r = r[1:]
        while r and r[-1] in ARITH:
            r = r[:-1]
        if not r or r.count("(") != r.count(")"):
            continue
        has_num = any(x[0].isdigit() for x in r)
        has_op = any(x in ARITH for x in r)
        has_fn = any(x in FUNCS for x in r)
        if has_num and (has_op or has_fn) and (best is None or len(r) > len(best)):
            best = r
    return " ".join(best) if best else None
