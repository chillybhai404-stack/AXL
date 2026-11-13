"""Storage: metadata DB, per-assistant data DBs, source files, encrypted vault, API keys, limits."""
import os, sqlite3, secrets, hashlib, time, json, collections
from cryptography.fernet import Fernet

DATA = os.environ.get("AXU_DATA", "data")
os.makedirs(DATA, exist_ok=True)


def _persist(name, maker):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(maker())
    with open(p) as f:
        return f.read().strip()


if os.environ.get("ADMIN_PASSWORD"):
    ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
else:
    ADMIN_PASSWORD = _persist("admin_password.txt", lambda: secrets.token_urlsafe(12))
    print(f"[AXU] Admin password: {ADMIN_PASSWORD}  (set ADMIN_PASSWORD env var to choose your own)")

# Vault encryption key. Set AXU_SECRET (a Fernet key) in production.
fernet = Fernet((os.environ.get("AXU_SECRET") or _persist("secret.key", lambda: Fernet.generate_key().decode())).encode())

STARTER_MAIN = '''# main.axl - your assistant. Rules run top to bottom; the first one that answers wins.
assistant: Shop Helper
import library
use math
use wikipedia

when asked about "price", "cost", "how much" -> find in menu -> "{name} costs Rs {price}. With tax: Rs {price * (1 + tax):2}."
when asked about "cheap", "cheapest" -> sql "SELECT name, price FROM menu ORDER BY price LIMIT 2" -> "{name} - Rs {price}"
when asked about "hello", "hi" -> say "Hi! Ask about the menu, or try math like: 12 * (3 + 4)"

if unsure -> say "Sorry, I don't know that yet."
'''

STARTER_LIB = '''# library.axl - your data library. main.axl pulls it in with: import library
let tax = 0.18

table menu (name, price, category)
row "Chilly Paneer", 220, "starter"
row "Butter Naan", 40, "bread"
row "Veg Biryani", 180, "main"
'''

_migrated = False


def meta():
    global _migrated
    con = sqlite3.connect(os.path.join(DATA, "meta.db"))
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS assistants(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, rules TEXT);
        CREATE TABLE IF NOT EXISTS sources(assistant_id INTEGER, name TEXT, content TEXT, PRIMARY KEY(assistant_id, name));
        CREATE TABLE IF NOT EXISTS keys(id INTEGER PRIMARY KEY AUTOINCREMENT, prefix TEXT, key_hash TEXT UNIQUE,
            label TEXT, assistants TEXT, daily_limit INTEGER, revoked INTEGER DEFAULT 0, created INTEGER, last_used INTEGER);
        CREATE TABLE IF NOT EXISTS vault(name TEXT PRIMARY KEY, secret BLOB, base_url TEXT);
        CREATE TABLE IF NOT EXISTS usage(key_id INTEGER, day TEXT, n INTEGER, PRIMARY KEY(key_id, day));
        """
    )
    if not _migrated:  # upgrade older databases in place
        for table, col, typ in (
            ("keys", "monthly_limit", "INTEGER DEFAULT 0"), ("keys", "total_limit", "INTEGER DEFAULT 0"),
            ("keys", "per_minute", "INTEGER DEFAULT 0"), ("keys", "expires_at", "INTEGER DEFAULT 0"),
            ("keys", "bundle", "TEXT DEFAULT '[]'"), ("keys", "bundle_mode", "TEXT DEFAULT 'failover'"),
            ("vault", "provider", "TEXT DEFAULT 'openai'"), ("vault", "model", "TEXT DEFAULT ''"),
        ):
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        con.commit()
        _migrated = True
    return con


def rows(sql, args=()):
    con = meta()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def write(sql, args=()):
    con = meta()
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


# ---- assistants' data + source files ----
def data_path(aid):
    return os.path.join(DATA, f"assistant_{int(aid)}.db")


def data_db(aid):
    con = sqlite3.connect(data_path(aid))
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS _tables(name TEXT PRIMARY KEY, kind TEXT, columns TEXT)")
    return con


def tables(aid):
    con = data_db(aid)
    try:
        return [dict(r) for r in con.execute("SELECT * FROM _tables ORDER BY name")]
    finally:
        con.close()


def sources(aid):
    r = rows("SELECT name, content FROM sources WHERE assistant_id=? ORDER BY name", (aid,))
    if not r:  # first open, or an assistant made before multi-file support
        old = rows("SELECT rules FROM assistants WHERE id=?", (aid,))
        text = (old[0]["rules"] if old and old[0]["rules"] else "") or STARTER_MAIN
        write("INSERT OR IGNORE INTO sources VALUES(?,?,?)", (aid, "main.axl", text))
        r = rows("SELECT name, content FROM sources WHERE assistant_id=? ORDER BY name", (aid,))
    return [dict(x) for x in r]


def put_source(aid, name, content):
    write("INSERT OR REPLACE INTO sources VALUES(?,?,?)", (aid, name, content))


def delete_source(aid, name):
    write("DELETE FROM sources WHERE assistant_id=? AND name=?", (aid, name))


# ---- vault ----
def vault_set(name, secret, base_url="", provider="openai", model=""):
    write("INSERT OR REPLACE INTO vault(name, secret, base_url, provider, model) VALUES(?,?,?,?,?)",
          (name, fernet.encrypt(secret.encode()), base_url or "", provider, model or ""))


def vault_entry(name):
    r = rows("SELECT * FROM vault WHERE name=?", (name,))
    if not r:
        raise KeyError(f"No vault entry named {name}")
    x = r[0]
    return {"name": x["name"], "provider": x["provider"] or "openai", "secret": fernet.decrypt(x["secret"]).decode(),
            "base_url": x["base_url"] or "", "model": x["model"] or ""}


# ---- keys ----
def hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def new_key(label, assistants, daily=0, monthly=0, total=0, per_minute=0, expires_days=0, bundle=(), bundle_mode="failover"):
    raw = "axu_live_" + secrets.token_urlsafe(32)
    now = int(time.time())
    write(
        "INSERT INTO keys(prefix,key_hash,label,assistants,daily_limit,monthly_limit,total_limit,per_minute,expires_at,created,bundle,bundle_mode)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (raw[:13], hash_key(raw), label, json.dumps(assistants), daily, monthly, total, per_minute,
         now + expires_days * 86400 if expires_days else 0, now, json.dumps(list(bundle)), bundle_mode),
    )
    return raw


def find_key(raw):
    r = rows("SELECT * FROM keys WHERE key_hash=?", (hash_key(raw),))
    return r[0] if r else None


def today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def bump(kid):
    con = meta()
    try:
        con.execute("INSERT INTO usage VALUES(?,?,1) ON CONFLICT(key_id,day) DO UPDATE SET n=n+1", (kid, today()))
        con.execute("UPDATE keys SET last_used=? WHERE id=?", (int(time.time()), kid))
        con.commit()
    finally:
        con.close()


# ---- limits ----
_hits = collections.defaultdict(collections.deque)


def rate_ok(kid, per_minute):
    if not per_minute:
        return True
    q, now = _hits[kid], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        return False
    q.append(now)
    return True


def usage(kid):
    r = rows("SELECT day, n FROM usage WHERE key_id=?", (kid,))
    t = today()
    return {
        "day": sum(x["n"] for x in r if x["day"] == t),
        "month": sum(x["n"] for x in r if x["day"][:7] == t[:7]),
        "total": sum(x["n"] for x in r),
    }


def check_limits(k):
    """Return (status, message) if this key can't be used right now, else None. Does not count the request."""
    if k["expires_at"] and time.time() > k["expires_at"]:
        return 401, "This key has expired"
    u = usage(k["id"])
    for col, name, label in (("daily_limit", "day", "Daily"), ("monthly_limit", "month", "Monthly"), ("total_limit", "total", "Total")):
        if k[col] and u[name] >= k[col]:
            return 429, f"{label} limit reached ({k[col]} responses)"
    if not rate_ok(k["id"], k["per_minute"]):
        return 429, f"Too many requests, slow down (max {k['per_minute']} per minute)"
    return None
