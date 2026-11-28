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
- **Browser-local AI Lab:** train a tiny decoder-only Transformer and a small CNN classifier directly in the browser with TensorFlow.js. Training, prediction and model storage use the visitor’s device, not the Render server.

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

A normal assistant key invokes the AXL server-side rule engine. A merged key invokes the provider bundle stored in the Vault. Browser-trained AI Lab models are private to the browser profile that trained them and are not exposed as server API endpoints. The `model` value is accepted for SDK compatibility; the merged key chooses the actual provider model from its Vault settings.

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

## AXL AI Lab: browser-local model training

The AI Lab is powered by **TensorFlow.js in the browser**. When the AI Lab opens, the page loads the TensorFlow.js runtime and JSZip in the visitor’s browser. The model's training and prediction operations run there; WebGL is selected for GPU-backed browser operations when available, with a CPU fallback. No Python, PyTorch, TensorFlow server package, or model-training service is required on Render.

### What stays on the phone/browser

- Text dataset files are read with the browser File API and are not uploaded.
- Vision ZIP files are opened and images are decoded in the browser.
- Transformer and CNN training execute through TensorFlow.js in the browser.
- Predictions execute in the same browser.
- Trained model weights are saved in browser IndexedDB, not under `AXU_DATA`.

The AI Lab model database is scoped to the website origin and browser profile. Models do not automatically follow the user to another phone, browser, or website. Clearing site data or browser storage can remove them. The first time AI Lab is opened, the browser needs internet access to load the TensorFlow.js runtime from its CDN. After it is loaded, AI Lab training and prediction run in the browser and do not send datasets, test images, prompts entered in the AI Lab, or trained weights to the AXL server. The Studio console is a separate feature: it sends a question to Render for existing AXL rule evaluation, and only runs a saved browser Transformer locally if the server rules return an unsure result.

### Text Transformer

AXL includes a compact **decoder-only causal Transformer** with byte-level UTF-8 tokenization (256 token values). It is trained from scratch using next-byte prediction. Defaults are deliberately small for phones: one Transformer layer, a 32-dimensional embedding, a 48-byte context and a small batch. The UI limits model sizes to reduce the risk of mobile browser memory exhaustion.

For question/answer experiments, format the training file as dialogue examples, for example:

```text
User: What is Earth Reboot?
Assistant: Earth Reboot is an environmental platform developed in Kashmir.

User: What does it help people do?
Assistant: It helps people identify waste and learn appropriate disposal methods.

User: Why include organic waste?
Assistant: Organic waste can be composted when suitable facilities and practices are available.
```

A starter file is included at `examples/dialogue_dataset_template.txt`; replace its placeholders with verified facts. Repeated, varied examples are more useful than a short list of facts. A small next-token model learns patterns in its training sequences; it does **not** automatically become a reliable question-answering assistant just because a document was uploaded. It may produce incoherent or incorrect output and is not comparable to a large pretrained assistant.

To use the browser-trained Transformer as the fallback in the AXL Studio console, add this to `main.axl`:

```axl
assistant: Bird AI
fallback transformer model Bird-Tiny
if unsure -> say "I don't know yet."
```

Train a text model named `Bird-Tiny` in AI Lab on the same browser first. The server evaluates AXL rules; if the rules do not answer and this fallback is selected, the Studio page generates the fallback response locally in that browser. A server API key cannot access a model saved in a user's browser.

### Vision classifier

The Vision Lab trains a small CNN **from scratch** using class-folder images. Upload a ZIP with this structure (a single wrapper folder is also accepted):

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

Use 2–12 classes and at least two images per class to start; 20–50+ varied, correctly labelled photos per class is a better starting point. The phone-first interface caps a model at 400 images and 128×128 pixels. Keep a separate test set outside the training ZIP. The classifier only predicts labels represented in its training folders; it does not explain materials, recognize every object, or determine local disposal rules. Its confidence is not a guarantee of correctness.

### Render deployment and resource boundary

Render still serves the AXL website and its existing FastAPI services (editor files, SQLite-backed assistants, provider vault, API-key management and server-side AXL rules). This update moves **AI Lab model training, AI Lab inference and AI Lab model storage** to the browser. Render does not receive the dataset or test image and does not run the TensorFlow model for these AI Lab actions. The browser downloads the runtime script when AI Lab is opened; TensorFlow.js then uses the browser WebGL backend (which can use the phone GPU when hardware acceleration is available) or falls back to CPU. A WebGL backend does not guarantee that the browser is using a physical GPU on every device. Hardware support, available memory, battery state and browser limits affect speed and maximum model size.

The Studio's server-side rule engine and provider-backed API gateway remain server-side. Browser-trained models are not callable from `/v1/chat` or an unrelated website through an API key, because their weights live in the browser's own IndexedDB. The Studio's console can use a saved local Transformer fallback on the same device/browser after server-side AXL rules have been checked.


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
- The browser-local Transformer and vision CNN are small experimental models, not general-purpose pretrained AI systems.
- AXL rule-based assistants are stateless: the OpenAI-compatible endpoint processes the latest user turn, while a merged provider key forwards the supplied messages to its configured provider.
- Provider APIs and model availability depend on the third-party provider.
- CORS is permissive (`*`) for browser integrations, but requests still require a valid Bearer key.
