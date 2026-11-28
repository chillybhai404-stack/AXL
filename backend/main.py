"""AXU Core - AXL studio, add-ons, key merger, vault and master keys (FastAPI)."""
import os, re, json, hmac, sqlite3, io, zipfile, time
from typing import List, Optional
from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from . import store, engine, ingest, addons, llm, ai_lab

app = FastAPI(title="AXU Core")

# Allow browser-based clients on other origins to call the API.
# Credentials are disabled because API access uses explicit bearer keys.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Declaring HTTPBearer makes FastAPI publish a real Bearer security scheme
# in OpenAPI. Swagger UI will then attach the Authorization header correctly.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")
FRONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
MAX_BYTES = 10 * 1024 * 1024


def admin(x_admin_password: str = Header("")):
    if not hmac.compare_digest(x_admin_password.encode(), store.ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "Wrong admin password")


A = [Depends(admin)]


# ---------- models ----------
class AssistantIn(BaseModel):
    name: str


class SourceIn(BaseModel):
    name: str
    content: str = ""


class RenameSourceIn(BaseModel):
    name: str


class DriveIn(BaseModel):
    url: str
    name: Optional[str] = None


class TestIn(BaseModel):
    q: str


class KeyIn(BaseModel):
    label: str = ""
    assistants: List[int] = []
    daily_limit: int = 1000
    monthly_limit: int = 0
    total_limit: int = 0
    per_minute: int = 0
    expires_days: int = 0
    bundle: List[str] = []
    bundle_mode: str = "failover"


class VaultIn(BaseModel):
    name: str
    secret: str
    provider: str = "openai"
    base_url: str = ""
    model: str = ""


class AskIn(BaseModel):
    q: str
    assistant: Optional[str] = None


class ChatIn(BaseModel):
    q: Optional[str] = None
    messages: Optional[List[dict]] = None
    assistant: Optional[str] = None
    max_tokens: int = 500
    model: Optional[str] = None
    stream: Optional[bool] = False


class TextGenerateIn(BaseModel):
    model: str
    prompt: str
    max_new_tokens: int = 160
    temperature: float = 0.8


def _vision_train_task(jid, dataset_root, cleanup_root, model_name, epochs, image_size, batch_size):
    try:
        ai_lab.train_vision(jid, str(dataset_root), model_name, epochs, image_size, batch_size)
    finally:
        import shutil
        shutil.rmtree(cleanup_root, ignore_errors=True)


# ---------- helpers ----------
def get_assistant(aid):
    r = store.rows("SELECT * FROM assistants WHERE id=?", (aid,))
    if not r:
        raise HTTPException(404, "No such assistant")
    return r[0]


def source_name(raw):
    n = raw.strip()
    n = n if n.lower().endswith(".axl") else n + ".axl"
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}\.axl", n):
        raise HTTPException(400, "File names can use letters, numbers, - and _ only")
    return n


def project_files(aid):
    return {f["name"]: f["content"] for f in store.sources(aid)}


# ---------- admin: assistants ----------
@app.get("/api/assistants", dependencies=A)
def list_assistants():
    return [{"id": r["id"], "name": r["name"], "tables": store.tables(r["id"])}
            for r in store.rows("SELECT id, name FROM assistants ORDER BY id")]


@app.post("/api/assistants", dependencies=A)
def create_assistant(b: AssistantIn):
    name = b.name.strip()
    if not name:
        raise HTTPException(400, "Give the assistant a name")
    try:
        aid = store.write("INSERT INTO assistants(name, rules) VALUES(?,?)", (name, ""))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That name is already used")
    store.put_source(aid, "main.axl", store.STARTER_MAIN.replace("Shop Helper", name, 1))
    store.put_source(aid, "library.axl", store.STARTER_LIB)
    return {"id": aid}


@app.delete("/api/assistants/{aid}", dependencies=A)
def delete_assistant(aid: int):
    get_assistant(aid)
    store.write("DELETE FROM assistants WHERE id=?", (aid,))
    store.write("DELETE FROM sources WHERE assistant_id=?", (aid,))
    if os.path.exists(store.data_path(aid)):
        os.remove(store.data_path(aid))
    return {"ok": True}


@app.get("/api/assistants/{aid}/sources", dependencies=A)
def get_sources(aid: int):
    get_assistant(aid)
    return store.sources(aid)


@app.put("/api/assistants/{aid}/sources", dependencies=A)
def put_source(aid: int, b: SourceIn):
    get_assistant(aid)
    store.sources(aid)  # makes sure main.axl exists
    store.put_source(aid, source_name(b.name), b.content[:200000])
    return {"errors": engine.validate(project_files(aid))}


@app.patch("/api/assistants/{aid}/sources/{name}", dependencies=A)
def rename_source(aid: int, name: str, b: RenameSourceIn):
    get_assistant(aid)
    if name == "main.axl":
        raise HTTPException(400, "main.axl must keep its name")
    current = {f["name"]: f["content"] for f in store.sources(aid)}
    if name not in current:
        raise HTTPException(404, "File not found")
    new_name = source_name(b.name)
    if new_name != name and new_name in current:
        raise HTTPException(409, "A file with that name already exists")
    content = current[name]
    if new_name != name:
        store.delete_source(aid, name)
        store.put_source(aid, new_name, content)
    return {"name": new_name, "errors": engine.validate(project_files(aid))}


@app.delete("/api/assistants/{aid}/sources/{name}", dependencies=A)
def del_source(aid: int, name: str):
    get_assistant(aid)
    if name == "main.axl":
        raise HTTPException(400, "main.axl can't be deleted")
    store.delete_source(aid, name)
    return {"errors": engine.validate(project_files(aid))}


@app.get("/api/assistants/{aid}/export", dependencies=A)
def export_assistant(aid: int):
    assistant = get_assistant(aid)
    sources = store.sources(aid)
    buffer = io.BytesIO()
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", assistant["name"]).strip("_") or "assistant"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for source in sources:
            zf.writestr(f"{safe_name}/{source['name']}", source["content"])
        manifest = {"assistant": assistant["name"], "exported_at": int(time.time()),
                    "files": [source["name"] for source in sources],
                    "note": "AXL source files only. Provider secrets and API keys are never included."}
        zf.writestr(f"{safe_name}/project.json", json.dumps(manifest, indent=2))
        zf.writestr(f"{safe_name}/README.txt", "AXL project export\n\nOpen main.axl in AXU Core.\nProvider secrets and generated API keys are not part of this export.\n")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-axl-project.zip"'})


@app.get("/api/assistants/{aid}/problems", dependencies=A)
def problems(aid: int):
    get_assistant(aid)
    return {"errors": engine.validate(project_files(aid))}


def _ingest(aid, filename, raw, name=None):
    con = store.data_db(aid)
    try:
        return ingest.ingest(con, filename, raw, name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "Couldn't read that file")
    finally:
        con.close()


@app.post("/api/assistants/{aid}/files", dependencies=A)
def upload(aid: int, file: UploadFile = File(...)):
    get_assistant(aid)
    raw = file.file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "File too large (10 MB max)")
    return _ingest(aid, file.filename or "upload.txt", raw)


@app.post("/api/assistants/{aid}/drive", dependencies=A)
def from_drive(aid: int, b: DriveIn):
    get_assistant(aid)
    try:
        filename, raw = ingest.fetch_drive(b.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(502, "Couldn't reach Google Drive")
    return _ingest(aid, filename, raw, b.name)


@app.delete("/api/assistants/{aid}/tables/{name}", dependencies=A)
def delete_table(aid: int, name: str):
    get_assistant(aid)
    con = store.data_db(aid)
    try:
        ingest.drop(con, name)
    finally:
        con.close()
    return {"ok": True}


@app.post("/api/assistants/{aid}/test", dependencies=A)
def test(aid: int, b: TestIn):
    get_assistant(aid)
    return engine.ask(aid, b.q[:500], debug=True)


@app.get("/api/addons", dependencies=A)
def list_addons():
    return addons.listing()


# ---------- admin: keys ----------
@app.get("/api/keys", dependencies=A)
def list_keys():
    names = {r["id"]: r["name"] for r in store.rows("SELECT id, name FROM assistants")}
    out = []
    for r in store.rows("SELECT * FROM keys ORDER BY id DESC"):
        ids = json.loads(r["assistants"])
        out.append({
            "id": r["id"], "prefix": r["prefix"], "label": r["label"], "revoked": bool(r["revoked"]),
            "scope": [names.get(i, f"#{i}") for i in ids] or ["all assistants"],
            "bundle": json.loads(r["bundle"] or "[]"), "mode": r["bundle_mode"],
            "limits": {"daily": r["daily_limit"], "monthly": r["monthly_limit"], "total": r["total_limit"],
                       "per_minute": r["per_minute"]},
            "usage": store.usage(r["id"]), "expires_at": r["expires_at"],
            "created": r["created"], "last_used": r["last_used"],
        })
    return out


@app.post("/api/keys", dependencies=A)
def create_key(b: KeyIn):
    have = {r["name"] for r in store.rows("SELECT name FROM vault")}
    if any(n not in have for n in b.bundle):
        raise HTTPException(400, "One of the selected vault keys no longer exists")
    mode = b.bundle_mode if b.bundle_mode in ("failover", "rotate") else "failover"
    n = lambda v: max(0, v)
    raw = store.new_key(b.label.strip(), b.assistants, n(b.daily_limit), n(b.monthly_limit), n(b.total_limit),
                        n(b.per_minute), n(b.expires_days), b.bundle, mode)
    record = store.find_key(raw)
    return {"key": raw, "id": record["id"] if record else None}


@app.post("/api/keys/{kid}/revoke", dependencies=A)
def revoke(kid: int):
    store.write("UPDATE keys SET revoked=1 WHERE id=?", (kid,))
    return {"ok": True}


# ---------- admin: vault ----------
@app.get("/api/vault", dependencies=A)
def list_vault():
    return [{"name": r["name"], "provider": r["provider"], "model": r["model"], "base_url": r["base_url"]}
            for r in store.rows("SELECT name, provider, model, base_url FROM vault ORDER BY name")]


@app.put("/api/vault", dependencies=A)
def put_vault(b: VaultIn):
    name = "".join(c for c in b.name if c.isalnum() or c == "_")
    if not name or not b.secret:
        raise HTTPException(400, "Name (letters, numbers, _) and key are required")
    provider = b.provider if b.provider in ("openai", "anthropic", "gemini", "groq", "openrouter", "custom") else "openai"
    store.vault_set(name, b.secret, b.base_url.strip(), provider, b.model.strip())
    return {"ok": True, "name": name}


@app.post("/api/vault/test", dependencies=A)
def test_vault(b: VaultIn):
    provider = b.provider if b.provider in ("openai", "anthropic", "gemini", "groq", "openrouter", "custom") else "openai"
    if not b.secret.strip():
        raise HTTPException(400, "Paste the provider API key first")
    result = llm.test_connection({"name": b.name or "test", "provider": provider,
                                 "secret": b.secret.strip(), "base_url": b.base_url.strip(),
                                 "model": b.model.strip()})
    if not result["ok"]:
        raise HTTPException(502, result["error"])
    return result


@app.delete("/api/vault/{name}", dependencies=A)
def delete_vault(name: str):
    store.write("DELETE FROM vault WHERE name=?", (name,))
    return {"ok": True}


# ---------- AI Lab: local Transformer + vision models ----------
@app.get("/api/ai/models", dependencies=A)
def ai_models():
    return ai_lab.list_models()


@app.post("/api/ai/text/train", dependencies=A)
def ai_train_text(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    model_name: str = Form(...),
    epochs: int = Form(3),
    block_size: int = Form(96),
    embedding_size: int = Form(64),
    attention_heads: int = Form(4),
    layers: int = Form(2),
    steps_per_epoch: int = Form(30),
):
    try:
        ai_lab.require_torch()
        name = ai_lab.slug(model_name)
        if ai_lab.text_model_dir(name).exists():
            raise HTTPException(409, "A text model with that name already exists. Choose another name.")
        raw = file.file.read(5 * 1024 * 1024 + 1)
        path = ai_lab.save_text_upload(raw, file.filename or "dataset.txt")
        jid = ai_lab.create_job("text", name)
        settings = {"epochs": epochs, "block_size": block_size, "embedding_size": embedding_size,
                    "attention_heads": attention_heads, "layers": layers, "steps_per_epoch": steps_per_epoch}
        background_tasks.add_task(ai_lab.train_text, jid, str(path), name, settings)
        return {"job_id": jid, "status": "queued", "model": name,
                "note": "Training runs on the AXL server. Keep the service running until it finishes."}
    except HTTPException:
        raise
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/ai/vision/train", dependencies=A)
def ai_train_vision(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    model_name: str = Form(...),
    epochs: int = Form(5),
    image_size: int = Form(96),
    batch_size: int = Form(16),
):
    try:
        ai_lab.require_torch()
        name = ai_lab.slug(model_name)
        if ai_lab.vision_model_dir(name).exists():
            raise HTTPException(409, "A vision model with that name already exists. Choose another name.")
        raw = file.file.read(60 * 1024 * 1024 + 1)
        root, cleanup_root = ai_lab.save_vision_zip(raw)
        jid = ai_lab.create_job("vision", name)
        background_tasks.add_task(_vision_train_task, jid, str(root), str(cleanup_root), name, epochs, image_size, batch_size)
        return {"job_id": jid, "status": "queued", "model": name,
                "note": "Training runs on the AXL server. Keep the service running until it finishes."}
    except HTTPException:
        raise
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/ai/jobs/{job_id}", dependencies=A)
def ai_job(job_id: str):
    result = ai_lab.get_job(job_id)
    if not result:
        raise HTTPException(404, "Training job not found. Jobs are held in memory and disappear after a server restart.")
    return result


@app.post("/api/ai/text/generate", dependencies=A)
def ai_generate_text(b: TextGenerateIn):
    try:
        return ai_lab.generate_text(b.model, b.prompt, b.max_new_tokens, b.temperature)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/ai/vision/predict", dependencies=A)
def ai_predict_vision(model_name: str = Form(...), file: UploadFile = File(...)):
    raw = file.file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "Image is limited to 10 MB")
    try:
        return ai_lab.predict_vision(model_name, raw)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


# ---------- public API (master keys) ----------
def _bearer(authorization):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Send your key as 'Authorization: Bearer <key>'")
    k = store.find_key(authorization[7:].strip())
    if not k or k["revoked"]:
        raise HTTPException(401, "Invalid or revoked key")
    return k


def key_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Send your key using the Authorize button as a Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    authorization = f"{credentials.scheme} {credentials.credentials}"
    k = _bearer(authorization)
    problem = store.check_limits(k)
    if problem:
        raise HTTPException(problem[0], problem[1])
    store.bump(k["id"])
    return k


def metadata_key_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    """Authenticate metadata reads without consuming a generation quota."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Send your key using Authorization: Bearer <key>", headers={"WWW-Authenticate": "Bearer"})
    k = _bearer(f"{credentials.scheme} {credentials.credentials}")
    if k["expires_at"] and time.time() > k["expires_at"]:
        raise HTTPException(401, "This key has expired")
    return k


def require_local_model_access(key):
    """Local model endpoints use standard unrestricted keys; scoped and merged keys stay scoped."""
    allowed_assistants = json.loads(key["assistants"] or "[]")
    bundle = json.loads(key["bundle"] or "[]")
    if allowed_assistants or bundle:
        raise HTTPException(403, "Local model endpoints require a standard API key with no assistant restriction and no provider bundle. Create a separate unrestricted API key for your local models.")


def resolve(key, ref):
    allowed = json.loads(key["assistants"])
    pool = [r for r in store.rows("SELECT id, name FROM assistants") if not allowed or r["id"] in allowed]
    if ref:
        pool = [r for r in pool if str(r["id"]) == ref or r["name"].lower() == ref.lower()]
    if len(pool) == 1:
        return pool[0]["id"]
    if not pool:
        raise HTTPException(403, "This key has no access to that assistant")
    raise HTTPException(400, "Add an 'assistant' (name or id) to choose which one to ask")


def _ask(key, q, ref):
    return engine.ask(resolve(key, ref), q[:500])


@app.post("/v1/ask")
def ask_post(b: AskIn, key=Depends(key_auth)):
    return _ask(key, b.q, b.assistant)


@app.get("/v1/ask")
def ask_get(q: str, assistant: Optional[str] = None, key=Depends(key_auth)):
    return _ask(key, q, assistant)


def _chat(key, b):
    if b.stream:
        raise HTTPException(400, "Streaming is not supported yet. Send stream=false or omit stream.")

    # Normalise OpenAI-style messages. Most website integrations send a history
    # array; AXL assistants are rule-based, so they answer the latest user turn.
    msgs = []
    for m in (b.messages or []):
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "user")).lower()
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if isinstance(content, (str, int, float)) and str(content).strip():
            msgs.append({"role": role, "content": str(content)})
    if not msgs and b.q:
        msgs = [{"role": "user", "content": b.q}]
    if not msgs:
        raise HTTPException(400, "Send 'q' or a non-empty 'messages' array")

    names = json.loads(key["bundle"] or "[]")
    if names:
        entries = []
        for n in names:
            try:
                entries.append(store.vault_entry(n))
            except KeyError:
                continue
        try:
            return llm.route(key["id"], entries, msgs, key["bundle_mode"], min(max(b.max_tokens, 1), 2000))
        except RuntimeError as e:
            raise HTTPException(502, str(e))

    # Non-merged API keys expose the AXL assistant runtime using the same
    # OpenAI-compatible endpoint, so the generated key works in website SDKs.
    user_turns = [m["content"] for m in msgs if m["role"] == "user"]
    question = user_turns[-1] if user_turns else msgs[-1]["content"]
    assistant_ref = b.assistant
    # Selecting a locally trained text model by its model name also works with
    # ordinary OpenAI-compatible clients at /v1/chat/completions.
    if not assistant_ref and b.model and ai_lab.text_model_dir(b.model).joinpath("model.pt").exists():
        require_local_model_access(key)
        system_text = "\n".join(m["content"] for m in msgs if m["role"] == "system")
        turns = [m for m in msgs if m["role"] in ("user", "assistant")][-8:]
        history = "\n".join(("User" if m["role"] == "user" else "Assistant") + ": " + m["content"] for m in turns)
        prompt = ((system_text + "\n") if system_text else "") + history + "\nAssistant:"
        try:
            generated = ai_lab.generate_text(b.model, prompt, max_new_tokens=min(max(b.max_tokens, 1), 200), temperature=0.8)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc))
        return {"answer": generated["continuation"].strip(), "provider": "AXL Transformer", "source": "local-transformer"}
    if not assistant_ref and b.model and b.model.lower() not in ("axl", "axl-assistant", "any"):
        assistant_ref = b.model
    aid = resolve(key, assistant_ref)
    result = engine.ask(aid, question[:500])
    return {"answer": result.get("answer", ""), "provider": "AXL", "source": result.get("source", "axl")}


@app.post("/v1/chat")
def chat(b: ChatIn, key=Depends(key_auth)):
    return _chat(key, b)


@app.get("/v1/chat")
def chat_get(q: str, assistant: Optional[str] = None, key=Depends(key_auth)):
    """Convenience GET route for quick browser/API-key smoke tests."""
    return _chat(key, ChatIn(q=q, assistant=assistant))


@app.post("/v1/chat/completions")
def chat_completions(b: ChatIn, key=Depends(key_auth)):
    r = _chat(key, b)
    return {"id": "chatcmpl-axu", "object": "chat.completion", "created": int(time.time()),
            "model": b.model or r.get("provider", "axl-assistant"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": r["answer"]}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


@app.get("/v1/models")
def models(key=Depends(metadata_key_auth)):
    bundle = json.loads(key["bundle"] or "[]")
    if bundle:
        ids = ["axu-merged"]
    else:
        allowed = json.loads(key["assistants"])
        rows = store.rows("SELECT id, name FROM assistants ORDER BY id")
        ids = [r["name"] for r in rows if not allowed or r["id"] in allowed]
        if not allowed:
            ids.extend(m["name"] for m in ai_lab.list_models()["text"] if m["name"] not in ids)
        if not ids:
            ids = ["axl-assistant"]
    return {"object": "list", "data": [{"id": model_id, "object": "model", "created": 0,
                                         "owned_by": "axu"} for model_id in ids]}


@app.get("/v1/me")
def me(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Send your key using the Authorize button as a Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    k = _bearer(f"{credentials.scheme} {credentials.credentials}")
    names = {r["id"]: r["name"] for r in store.rows("SELECT id, name FROM assistants")}
    ids = json.loads(k["assistants"])
    return {
        "label": k["label"], "prefix": k["prefix"], "created": k["created"], "last_used": k["last_used"],
        "assistants": [names[i] for i in ids if i in names] if ids else list(names.values()),
        "merged_keys": json.loads(k["bundle"] or "[]"),
        "limits": {"daily": k["daily_limit"], "monthly": k["monthly_limit"], "total": k["total_limit"],
                   "per_minute": k["per_minute"]},
        "usage": store.usage(k["id"]), "expires_at": k["expires_at"],
    }


# Public local-model endpoints. They use the same axu_live_ keys and usage limits.
@app.get("/v1/ai/models")
def public_ai_models(key=Depends(metadata_key_auth)):
    require_local_model_access(key)
    return ai_lab.list_models()


@app.post("/v1/ai/text/generate")
def public_ai_generate_text(b: TextGenerateIn, key=Depends(key_auth)):
    require_local_model_access(key)
    try:
        return ai_lab.generate_text(b.model, b.prompt, b.max_new_tokens, b.temperature)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/v1/ai/vision/predict")
def public_ai_predict_vision(model_name: str = Form(...), file: UploadFile = File(...), key=Depends(key_auth)):
    require_local_model_access(key)
    raw = file.file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "Image is limited to 10 MB")
    try:
        return ai_lab.predict_vision(model_name, raw)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/v1")
def api_info():
    return {"name": "AXU Core API", "status": "ok", "endpoints": {
        "chat": "POST /v1/chat", "openai_compatible": "POST /v1/chat/completions",
        "models": "GET /v1/models", "assistant": "GET or POST /v1/ask", "account": "GET /v1/me",
        "local_text_model": "POST /v1/ai/text/generate", "local_vision_model": "POST /v1/ai/vision/predict"}}


@app.get("/v1/health")
@app.get("/health")
def health():
    return {"ok": True}


app.mount("/", StaticFiles(directory=FRONT, html=True), name="ui")
