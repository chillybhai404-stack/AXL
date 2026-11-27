# AXL Studio update

## API and provider reliability

- Added a single OpenAI-compatible chat endpoint that dispatches both ordinary AXL assistant keys and merged provider keys.
- Added `GET /v1/models` for OpenAI SDK/model-picker compatibility.
- Added `GET /v1/chat?q=...` as a simple browser/API-key smoke test. The standard `/v1/chat/completions` endpoint remains `POST`.
- Added the `assistant` request field and support for selecting an AXL assistant by setting `model` to its assistant name.
- Added a test action for newly generated gateway keys and an admin-only provider Vault connection test.
- Improved API error messages for HTTP status errors, invalid provider URLs, timeouts and connection failures.
- Normalised OpenAI-compatible provider base URLs so API roots, `/v1` roots and full chat-completions URLs do not get double-appended.
- Added Anthropic base URL handling for both API roots and `/v1` roots.
- Enabled and smoke-tested cross-origin preflight for calls from separate websites.
- Fixed `run.py` import typo and made `ADMIN_PASSWORD` work as documented, keeping `ANSAR` as a legacy alias.

## Studio and file management

- Added a touch-first mobile layout with a slide-out Files drawer and Files / Editor / Console / Save / Export dock.
- Added file search, rename, delete, current `.axl` file download and whole-project ZIP export.
- Added a visible Save and Export Project toolbar.
- Project exports contain AXL source files and a project manifest only; provider secrets and generated API keys are excluded.

## Compatibility notes

- AXL rule-based assistants are stateless and process the latest user message on the OpenAI-compatible endpoint.
- Merged provider keys forward the supplied message list to the selected provider bundle.
- Streaming is not supported; use `stream: false` or omit the field.
- Provider model availability changes; use the Vault's Test connection button and confirm the provider's current model ID when a model is rejected.
