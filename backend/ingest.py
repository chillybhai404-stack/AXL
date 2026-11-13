"""Turn JSON / CSV / DOCX / PDF / TXT / Google Drive links into queryable tables."""
import re, io, csv, json, sqlite3
import httpx


def safe(s):
    s = re.sub(r"\W+", "_", str(s).lower()).strip("_")
    return s if s and not s[0].isdigit() else "t_" + s


def ingest(con, filename, raw, name=None):
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    ext = ext.lower()
    table = safe(name or stem)
    if ext in ("json", "csv"):
        return _rows(con, table, ext, raw)
    if ext in ("pdf", "docx", "txt", "md"):
        return _text(con, table, ext, raw)
    raise ValueError("Unsupported file type. Use .json .csv .docx .pdf .txt or .md")


def _register(con, table, kind, columns):
    con.execute("INSERT OR REPLACE INTO _tables VALUES(?,?,?)", (table, kind, columns))
    con.commit()


def _rows(con, table, ext, raw):
    if ext == "json":
        data = json.loads(raw.decode("utf-8-sig"))
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
            data = lists[0] if lists else [data]
        if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
            raise ValueError("JSON must be a list of objects, like [{...}, {...}]")
    else:
        data = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        if not data:
            raise ValueError("CSV is empty")
    clean = []
    cols = []
    for r in data:
        row = {}
        for k, v in r.items():
            c = safe(k)
            row[c] = json.dumps(v) if isinstance(v, (dict, list)) else v
            if c not in cols:
                cols.append(c)
        clean.append(row)
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute(f'CREATE TABLE "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)})')
    con.executemany(
        f'INSERT INTO "{table}" VALUES ({",".join("?" * len(cols))})',
        [[r.get(c) for c in cols] for r in clean],
    )
    _register(con, table, "rows", ", ".join(cols))
    return {"table": table, "kind": "rows", "rows": len(clean), "columns": cols}


def extract(ext, raw):
    if ext == "pdf":
        from pypdf import PdfReader

        return "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages)
    if ext == "docx":
        from docx import Document

        d = Document(io.BytesIO(raw))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n\n".join(parts)
    return raw.decode("utf-8-sig", errors="ignore")


def chunk(text, size=600):
    out = []
    for p in re.split(r"\n\s*\n", text):
        p = " ".join(p.split())
        if not p:
            continue
        if len(p) <= size:
            out.append(p)
            continue
        cur = ""
        for s in re.split(r"(?<=[.!?])\s+", p):
            if cur and len(cur) + len(s) > size:
                out.append(cur)
                cur = s
            else:
                cur = (cur + " " + s).strip()
        if cur:
            out.append(cur)
    return out


def _text(con, table, ext, raw):
    parts = chunk(extract(ext, raw))
    if not parts:
        raise ValueError("No readable text found (scanned PDFs need OCR first)")
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    try:
        con.execute(f"CREATE VIRTUAL TABLE \"{table}\" USING fts5(text, tokenize='porter')")
    except sqlite3.OperationalError:
        con.execute(f'CREATE TABLE "{table}" (text TEXT)')
    con.executemany(f'INSERT INTO "{table}"(text) VALUES (?)', [(p,) for p in parts])
    _register(con, table, "text", "text")
    return {"table": table, "kind": "text", "rows": len(parts), "columns": ["text"]}


def drop(con, table):
    table = safe(table)
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute("DELETE FROM _tables WHERE name=?", (table,))
    con.commit()


def fetch_drive(url):
    """Download a publicly shared Google Drive / Docs / Sheets link."""
    m = re.search(r"/d/([\w-]+)", url) or re.search(r"[?&]id=([\w-]+)", url)
    if not m:
        raise ValueError("That doesn't look like a Google Drive or Docs link")
    fid, name = m[1], ""
    if "docs.google.com/document" in url:
        u, name = f"https://docs.google.com/document/d/{fid}/export?format=docx", "gdoc.docx"
    elif "docs.google.com/spreadsheets" in url:
        u, name = f"https://docs.google.com/spreadsheets/d/{fid}/export?format=csv", "gsheet.csv"
    else:
        u = f"https://drive.google.com/uc?export=download&id={fid}"
    r = httpx.get(u, follow_redirects=True, timeout=30)
    if r.status_code != 200 or "text/html" in r.headers.get("content-type", ""):
        raise ValueError("Couldn't download it. Set sharing to 'Anyone with the link' and try again.")
    if not name:
        mm = re.search(r'filename="?([^";]+)', r.headers.get("content-disposition", ""))
        head = r.content[:4]
        name = mm[1] if mm else (
            "drive_file.pdf" if head == b"%PDF" else "drive_file.docx" if head[:2] == b"PK"
            else "drive_file.json" if head[:1] in (b"[", b"{") else "drive_file.txt"
        )
    return name, r.content
