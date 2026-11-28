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
- **AI Lab (optional):** train a tiny decoder-only Transformer on text and a small CNN image classifier from your own labelled images; save and serve local models through AXL.

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

For an AXL key, `model` can be the assistant name shown by `GET /v1/models`. A standard unrestricted key can also select a locally trained text model name. If the key can access more than one assistant, choose the assistant by setting `model` to its name, or include the custom field `assistant` with its name or ID:

```json
{
  "model": "axl-assistant",
  "assistant": "Shop Helper",
  "messages": [{"role": "user", "content": "Hello"}]
}
```

A normal assistant key invokes the AXL rule engine. A merged key invokes the provider bundle stored in the Vault. A standard unrestricted key can also call a local text model by setting `model` to its saved model name in `/v1/chat/completions`; local image classification is available through `/v1/ai/vision/predict`. The `model` value is accepted for SDK compatibility; the merged key chooses the actual provider model from its Vault settings.

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
| `GET` | `/v1/ai/models` | List locally trained models |
| `POST` | `/v1/ai/text/generate` | Generate with a local Transformer |
| `POST` | `/v1/ai/vision/predict` | Classify an uploaded image with a local CNN |
| `GET` | `/v1` | API endpoint overview |
| `GET` | `/v1/health` | Health check |

The OpenAI-compatible creation endpoint is **POST**, not GET. The editor's new **Test this key** button sends a POST automatically. Streaming is not implemented; send `stream: false` or omit it.

## Provider Vault setup

For OpenAI-compatible providers (OpenAI, Groq, OpenRouter and custom compatible services), set the Base URL to the API root, for example `https://api.groq.com/openai/v1`, or the full `/chat/completions` URL. Do not enter a `/models` URL. For Anthropic, use `https://api.anthropic.com`; for Gemini, use `https://generativelanguage.googleapis.com`. The Vault preserves the selected provider label while sending OpenAI-compatible providers through the shared adapter.

The Vault now has a **Test connection** action. It sends a tiny prompt and displays provider HTTP/connectivity errors before you save the key. This helps distinguish an invalid API key, invalid model, malformed Base URL, provider outage and a network connection failure.

Default model suggestions in the provider form are editable. Provider model availability changes over time; check your provider's current model catalogue when a model returns a 400 or 404.

## AXL AI Lab: local model training

The AI Lab is an **optional** PyTorch-powered part of AXL. The ordinary editor, rules engine, provider vault and API-key gateway still run without PyTorch. To enable local training and inference, install the optional dependencies:

```bash
pip install -r requirements-ai.txt
```

For a specific CUDA/GPU build, choose the matching command from the official [PyTorch installation selector](https://pytorch.org/get-started/locally/) rather than guessing a CUDA wheel.

Restart the service and open **AI Lab** in the Studio. Model files are saved under `AXU_DATA/ai_models/`; use a persistent disk in production. Training jobs are kept in memory, so their status disappears if the server restarts. Only one local training job runs at a time.

### Text Transformer

AXL currently includes a small **decoder-only causal Transformer** with byte-level UTF-8 tokenization. It is trained from scratch using next-byte prediction. It is deliberately small and educational: it is not a GPT-class conversational model, does not have a pretrained language understanding capability, and may generate incoherent output unless its dataset and training are appropriate. CPU training can be slow. Start with a small model and a text dataset of at least a few thousand characters.

For question/answer experiments, format the training file as dialogue examples, for example:

```text
User: What is Earth Reboot?
Assistant: Earth Reboot is an environmental platform developed in Kashmir.

User: What does it help people do?
Assistant: It helps people identify waste and learn appropriate disposal methods.

User: Why include organic waste?
Assistant: Organic waste can be composted when suitable facilities and practices are available.
```

A starter file is included at `examples/dialogue_dataset_template.txt`; replace its placeholders with your verified project facts. Repeated, varied examples are much more useful than a short list of facts. A small next-token model learns patterns in the training sequences; it does **not** automatically become a reliable question-answering assistant just because a document was uploaded. For dependable factual answers, keep the existing provider-backed model or implement a retrieval system alongside the local model.

In `main.axl`, a local model can be selected as an experimental fallback:

```axl
assistant: Bird AI
instructions "Continue the supplied dialogue format. Do not invent project facts."
fallback transformer model Bird-Tiny
if unsure -> say "I don't know yet."
```

The local fallback prompts the model with `User: ...` and `Assistant:`. It only works if the named text model has already been trained and saved.

### Vision classifier

The Vision Lab trains a small CNN **from scratch** using class-folder images. Upload a ZIP with this structure (a single top-level dataset folder is also accepted):

```text
plastic/
  bottle_01.jpg
  wrapper_01.jpg
paper/
  newspaper_01.jpg
  cardboard_01.jpg
organic/
  peels_01.jpg
  leaves_01.jpg
```

Use at least two classes and at least two valid images per class to start; dozens or hundreds of varied, correctly labelled images per class are a much better target. Keep a separate test set outside the training ZIP to measure real-world performance. Avoid near-duplicate photos across training and test sets. The classifier only predicts the labels it was trained on; it does not explain materials, identify every object, or determine local disposal rules. Its confidence is not a guarantee of correctness.

### Local model API

The same generated `axu_live_...` API keys can call local-model endpoints. These requests count against the key's configured usage limits.

Text generation:

```http
POST /v1/ai/text/generate
Authorization: Bearer axu_live_YOUR_KEY
Content-Type: application/json
```

```json
{
  "model": "Bird-Tiny",
  "prompt": "User: What is Earth Reboot?\nAssistant:",
  "max_new_tokens": 120,
  "temperature": 0.7
}
```

Vision prediction uses multipart form data:

```http
POST /v1/ai/vision/predict
Authorization: Bearer axu_live_YOUR_KEY
```

Form fields: `model_name=EarthReboot-Vision` and `file=<image>`. Model metadata is available at `GET /v1/ai/models`. Standalone local-model endpoints require a standard API key with no assistant restriction (leave the assistant selector empty when creating the key) and no provider bundle. Assistant-scoped keys can still reach a local Transformer through that assistant’s `fallback transformer model` rule. Per-model permissions are not implemented yet. Keep keys private and revoke keys that are no longer needed.

### Hardware and deployment

The small defaults are intended for experimentation. Training runs on the AXL server, not in the browser. A free/shared CPU web host may time out, run out of RAM, or be too slow for training; hosted platforms may also have ephemeral storage unless a persistent disk is configured. For serious training, use a machine with sufficient RAM and preferably a supported GPU. Do not expose the admin routes publicly without a strong admin password, HTTPS and sensible upload limits.


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
- The optional local Transformer and vision CNN are small experimental models, not general-purpose pretrained AI systems.
- AXL rule-based assistants are stateless: the OpenAI-compatible endpoint processes the latest user turn, while a merged provider key forwards the supplied messages to its configured provider.
- Provider APIs and model availability depend on the third-party provider.
- CORS is permissive (`*`) for browser integrations, but requests still require a valid Bearer key.
