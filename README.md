# AXU Core

A professional workspace for building assistants without an LLM bill.

- **Studio**: a VS Code style editor for the AXL language (syntax colours, line numbers, auto-closing quotes and brackets, live error checking, multiple files).
- **Add-ons**: one click gives an assistant Wikipedia, weather, dictionary, countries, currency, books, jokes and math.
- **Key Merger**: put several AI provider keys (OpenAI, Anthropic, Gemini, Groq, OpenRouter...) into one AXU key, with failover or rotation. It speaks the OpenAI format.
- **Vault**: provider keys stored encrypted. Callers never see them.
- **API keys**: per-app keys with limits per day, month, total, minute, and an expiry.
- **Data**: upload JSON, CSV, DOCX, PDF, TXT, MD or a shared Google Drive link.

Backend: FastAPI + SQLite. Frontend: plain HTML/CSS/JS, designed for desktop browsers.

## Run it

```
pip install -r requirements.txt
python run.py
```
Open http://127.0.0.1:8000. The admin password is printed on first start and saved in `data/admin_password.txt`. Choose your own with the `ADMIN_PASSWORD` environment variable.

## The AXL language

Files end in `.axl`. `main.axl` is your assistant; other files are libraries it can import.

```
# main.axl
assistant: Shop Helper
import library                # loads library.axl (its tables, lets and rules)
use math                      # add-ons: math weather dictionary countries currency books joke wikipedia
use wikipedia

when asked about "price", "cost" -> find in menu -> "{name} costs Rs {price}. With tax: Rs {price * (1 + tax):2}."
when asked about "cheap"         -> sql "SELECT name, price FROM menu ORDER BY price LIMIT 2" -> "{name} - Rs {price}"
when asked about "premium"       -> find in menu where price > 150 -> "{name} is premium"
when asked about "refund"        -> search policies -> "{text}"
when asked about "hello"         -> say "Hi!"

if unsure -> say "Sorry, I don't know that yet."
```
```
# library.axl
let tax = 0.18
table menu (name, price, category)
row "Chilly Paneer", 220, "starter"
row "Butter Naan", 40, "bread"
```

| Line | What it does |
|---|---|
| `assistant: Name` | Names the assistant |
| `import file` | Loads `file.axl` (tables, lets, rules) |
| `let x = expr` | A constant; can use math and earlier lets |
| `table name (a, b)` + `row v1, v2` | Inline data table |
| `use addon` | Turns on an add-on |
| `when asked about "a", "b" -> action` | A rule. Use `anything` to match every question |
| `if unsure -> say "..."` | Final answer when nothing matched |
| `fallback llm vault:NAME model M` | Last resort: ask an AI model (the only part that costs money) |

Actions: `say "text"`, `math`, `find in TABLE [where condition]`, `search DOCUMENT_TABLE`, `sql "SELECT ..."` (read-only).

**Math and relations** work anywhere: in `let`, in `where`, and inside `{ }` in answers.
`+ - * / % ^`, brackets, `sqrt abs round floor ceil min max sin cos tan log`, comparisons `< > <= >= == !=`, `and or not`, `if(cond, a, b)`, and `{expr:2}` for 2 decimals.
With `use math`, users can ask questions like `(x * 15 = 10)` (answer: `x = 0.6667`), `2x + 3 = 11`, `x^2 = 9`, `12 * (3 + 4)` or `15% of 200`.

In the editor: typing `"` gives `""`, `(` gives `()`, Ctrl+S saves (it also autosaves), and the **Problems** tab shows errors with line numbers.

## Key Merger

1. **Vault** tab: add each provider key (name, provider, key, model).
2. **Key Merger** tab: tick the keys, set the order and strategy, and merge. You get one `axu_live_...` key.
3. Use it with any OpenAI SDK:
```
from openai import OpenAI
client = OpenAI(base_url="https://YOUR-SERVER/v1", api_key="axu_live_...")
client.chat.completions.create(model="any", messages=[{"role": "user", "content": "Hello"}])
```
Or call `POST /v1/chat` with `{"q": "Hello"}`. **Failover** tries the keys in order and skips ones that fail. **Rotate** spreads requests across them. Streaming is not supported yet.

## Public API

```
GET  /v1/ask?q=...                 assistants (Authorization: Bearer KEY)
POST /v1/ask                       {"q": "...", "assistant": "name"}
POST /v1/chat                      merged AI keys
POST /v1/chat/completions          OpenAI-compatible
GET  /v1/me                        limits, usage, what the key can reach
```
Keep keys in a backend or environment variable, never in browser code.

## Deploy

- Any host: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
- Docker: `docker build -t axu-core . && docker run -p 8000:8000 -v axu:/data -e ADMIN_PASSWORD=... axu-core`
- **Keep `data/` on a persistent disk**, or restarts will delete your keys and files.
- Set `ADMIN_PASSWORD` and `AXU_SECRET` (a Fernet key) as environment variables. If `AXU_SECRET` changes, stored vault keys can't be decrypted.
- Serve over HTTPS.

## Limits to know

- Add-ons that call free web APIs (weather, dictionary, countries, currency, books, jokes, Wikipedia) and the AI providers depend on those services being reachable.
- Google Drive import works for files shared as "Anyone with the link".
- Scanned PDFs have no text and need OCR first.
- Rules match words, not meaning.
- Check each provider's terms before letting other people reach their API through your keys.
