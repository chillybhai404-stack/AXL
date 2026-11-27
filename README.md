# AXU Core / AXL Studio

AXU Core is a small FastAPI service with a browser-based AXL editor, assistant runtime, encrypted provider vault and an API-key gateway.

## What is included

- **Studio:** multi-file `.axl` editor, syntax highlighting, line numbers, auto-pairs, autosave, console and problem list.
- **Mobile Studio:** touch-sized file drawer, editor/console dock, file search, save controls and visible file/project export actions.
- **Project files:** create and delete AXL files, download the current file, or export all AXL source files as a ZIP. Exports do not contain API keys or provider secrets.
- **Data:** import JSON, CSV, DOCX, PDF, TXT, MD and a shared Google Drive link into an assistant's local SQLite database.
- **Add-ons:** one-click add-ons for Wikipedia, weather, dictionary, countries, currency, books, jokes and math.
- **API keys:** separate keys for each external website/app, with assistant scope, usage limits, expiry and revocation.
- **Provider Vault:** OpenAI-compatible, Anthropic and Gemini keys are encrypted at rest. Use **Test connection** before saving.
- **Key Merger:** combine provider keys with failover or rotation and receive one `axu_live_...` gateway key.

## Run locally

```bash
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:8000`. On first start, a random admin password is printed and stored in `data/admin_password.txt`. Set `ADMIN_PASSWORD` to choose one. `ANSAR` remains accepted as a backward-compatible environment variable.

For production, set a strong `ADMIN_PASSWORD`, set a persistent `AXU_SECRET` Fernet key, keep `AXU_DATA` on a persistent disk and serve over HTTPS. Do not replace `AXU_SECRET` after saving provider keys: the existing encrypted vault values will no longer decrypt.

## API keys for another website

Generate a key in **API Keys** for an AXL assistant, or in **Key Merger** for a provider-backed key. A generated key is shown once. Copy it and use the same gateway endpoint from another server or website backend.

### OpenAI-compatible endpoint (recommended)

`POST https://YOUR-SERVER/v1/chat/completions`

Headers:

```http
Authorization: Bearer axu_live_YOUR_KEY
Content-Type: application/json
```

Body:

```json
{
  "model": "Shop Helper",
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

For an AXL key, `model` can be the assistant name shown by `GET /v1/models`. If the key can access more than one assistant, choose the assistant by setting `model` to its name, or include the custom field `assistant` with its name or ID:

```json
{
  "model": "axl-assistant",
  "assistant": "Shop Helper",
  "messages": [{"role": "user", "content": "Hello"}]
}
```

A normal assistant key invokes the AXL rule engine. A merged key invokes the provider bundle stored in the Vault. Both return an OpenAI Chat Completions-shaped response. The `model` value is accepted for SDK compatibility; the merged key chooses the actual provider model from its Vault settings.

### Browser JavaScript example

```js
const response = await fetch("https://YOUR-SERVER/v1/chat/completions", {
  method: "POST",
  headers: {
    "Authorization": "Bearer axu_live_YOUR_KEY",
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    model: "Shop Helper",
    messages: [{ role: "user", content: "Hello" }]
  })
});

if (!response.ok) throw new Error(await response.text());
const data = await response.json();
console.log(data.choices[0].message.content);
```

**Security:** a key placed in browser JavaScript is visible to visitors. For a public website, keep the AXL key on your own server and call AXL from your backend. CORS is enabled for cross-origin requests; credentials/cookies are not used.

### Other public endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat response |
| `POST` | `/v1/chat` | Simple chat JSON (`q` or `messages`) |
| `GET` | `/v1/chat?q=...` | Quick browser smoke test |
| `GET` / `POST` | `/v1/ask` | Ask an AXL assistant directly |
| `GET` | `/v1/models` | Models visible to this key |
| `GET` | `/v1/me` | Key scope, limits and usage |
| `GET` | `/v1` | API endpoint overview |
| `GET` | `/v1/health` | Health check |

The OpenAI-compatible creation endpoint is **POST**, not GET. The editor's new **Test this key** button sends a POST automatically. Streaming is not implemented; send `stream: false` or omit it.

## Provider Vault setup

For OpenAI-compatible providers (OpenAI, Groq, OpenRouter and custom compatible services), set the Base URL to the API root, for example `https://api.groq.com/openai/v1`, or the full `/chat/completions` URL. Do not enter a `/models` URL. For Anthropic, use `https://api.anthropic.com`; for Gemini, use `https://generativelanguage.googleapis.com`. The Vault preserves the selected provider label while sending OpenAI-compatible providers through the shared adapter.

The Vault now has a **Test connection** action. It sends a tiny prompt and displays provider HTTP/connectivity errors before you save the key. This helps distinguish an invalid API key, invalid model, malformed Base URL, provider outage and a network connection failure.

Default model suggestions in the provider form are editable. Provider model availability changes over time; check your provider's current model catalogue when a model returns a 400 or 404.

## AXL language quick example

```axl
# main.axl
assistant: Shop Helper
import library
use math

when asked about "price", "cost" -> find in menu -> "{name} costs Rs {price}."
when asked about "hello", "hi" -> say "Hi!"
if unsure -> say "Sorry, I don't know that yet."
```

## Deploy

```bash
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

Or build/run the included Dockerfile. Keep `data/` on a persistent volume. Set `ADMIN_PASSWORD` and `AXU_SECRET`; use HTTPS. Do not commit `data/admin_password.txt`, `data/secret.key`, or provider secrets to a public repository.

## Important limitations

- Streaming responses are not implemented.
- AXL rule-based assistants are stateless: the OpenAI-compatible endpoint processes the latest user turn, while a merged provider key forwards the supplied messages to its configured provider.
- Provider APIs and model availability depend on the third-party provider.
- CORS is permissive (`*`) for browser integrations, but requests still require a valid Bearer key.
