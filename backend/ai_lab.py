"""AXL AI Lab: small educational Transformer and image classifier training.

PyTorch is optional. The core AXL app still starts without it; AI endpoints return
an actionable error until requirements-ai.txt is installed.

Text model: byte-level, decoder-only GPT-style Transformer trained with next-byte
prediction. This is an educational tiny model, not a production chat LLM.
Vision model: small CNN trained from scratch on class-folder images.
"""
from __future__ import annotations

import json
import importlib.util
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

torch = None


def require_torch():
    """Import the optional, heavyweight ML runtime only when a model is used."""
    global torch
    if torch is None:
        try:
            import torch as torch_module
        except Exception as exc:
            raise RuntimeError("AI training is optional. Install the packages in requirements-ai.txt, then restart AXL.") from exc
        torch = torch_module
    return torch


_DATA = Path(os.environ.get("AXU_DATA", "data"))
_MODELS = _DATA / "ai_models"
_DATASETS = _DATA / "ai_datasets"
_MODELS.mkdir(parents=True, exist_ok=True)
_DATASETS.mkdir(parents=True, exist_ok=True)
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_train_lock = threading.Lock()


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", (value or "").strip()).strip("-_")[:48]
    if not value:
        raise ValueError("Give the model a name using letters, numbers, - or _")
    return value


def text_model_dir(name: str) -> Path:
    return _MODELS / "text" / slug(name)


def vision_model_dir(name: str) -> Path:
    return _MODELS / "vision" / slug(name)


def list_models():
    out = {"text": [], "vision": [], "torch_available": torch is not None or importlib.util.find_spec("torch") is not None}
    for kind in ("text", "vision"):
        root = _MODELS / kind
        if not root.exists():
            continue
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            meta_path = folder / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
                meta["kind"] = kind
                out[kind].append(meta)
            except Exception:
                continue
    return out


def create_job(kind: str, name: str) -> str:
    jid = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[jid] = {"id": jid, "kind": kind, "model": name, "status": "queued", "progress": 0,
                      "message": "Waiting to start", "created_at": int(time.time())}
    return jid


def update_job(jid: str, **values):
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].update(values, updated_at=int(time.time()))


def get_job(jid: str):
    with _jobs_lock:
        item = _jobs.get(jid)
        return dict(item) if item else None


def save_text_upload(raw: bytes, filename: str) -> Path:
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("Text training files are limited to 5 MB")
    ext = Path(filename or "dataset.txt").suffix.lower()
    if ext not in (".txt", ".md", ".csv"):
        raise ValueError("Use a .txt, .md or .csv text dataset")
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Dataset must be UTF-8 text")
    if len(decoded.strip()) < 500:
        raise ValueError("Dataset is too small. Add at least 500 characters; larger, varied datasets are better")
    dest = _DATASETS / f"text-{uuid.uuid4().hex}.txt"
    dest.write_text(decoded, encoding="utf-8")
    return dest


def save_vision_zip(raw: bytes) -> Path:
    if len(raw) > 60 * 1024 * 1024:
        raise ValueError("Vision dataset ZIP is limited to 60 MB")
    target = _DATASETS / f"vision-{uuid.uuid4().hex}"
    target.mkdir(parents=True, exist_ok=False)
    max_uncompressed = 250 * 1024 * 1024
    max_entries = 5000
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    try:
        import io
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            infos = zf.infolist()
            if len(infos) > max_entries or sum(i.file_size for i in infos) > max_uncompressed:
                raise ValueError("Dataset ZIP is too large or contains too many files")
            extracted_images = 0
            for info in infos:
                if info.is_dir():
                    continue
                rel = PurePosixPath(info.filename)
                if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                    raise ValueError("Unsafe path found inside ZIP")
                if any(part.startswith(".") or part == "__MACOSX" for part in rel.parts):
                    continue
                if info.file_size > 20 * 1024 * 1024:
                    raise ValueError("An image inside the ZIP is larger than 20 MB")
                if Path(rel.name).suffix.lower() not in allowed:
                    continue
                # Require class/image.ext or wrapper/class/image.ext.
                if len(rel.parts) < 2:
                    continue
                dest = target.joinpath(*rel.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted_images += 1
        if extracted_images < 8:
            raise ValueError("ZIP needs at least 8 images arranged in class folders")
        # Unwrap a single top-level directory when the ZIP has dataset/class/image.
        children = list(target.iterdir())
        root = target
        if len(children) == 1 and children[0].is_dir():
            nested = children[0]
            if any(p.is_dir() for p in nested.iterdir()):
                root = nested
        class_dirs = [p for p in root.iterdir() if p.is_dir()]
        if len(class_dirs) < 2:
            raise ValueError("Put images inside at least two class folders, e.g. plastic/ and paper/")
        return root, target
    except Exception as exc:
        shutil.rmtree(target, ignore_errors=True)
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Could not read the vision ZIP: {str(exc)[:180]}")


def _text_model(config):
    """Build a causal decoder-only Transformer over UTF-8 bytes (0..255)."""
    torch = require_torch()
    nn = torch.nn
    F = torch.nn.functional

    class CausalSelfAttention(nn.Module):
        def __init__(self):
            super().__init__()
            emb, heads, block = config["embedding_size"], config["attention_heads"], config["block_size"]
            self.heads = heads
            self.head_dim = emb // heads
            self.qkv = nn.Linear(emb, emb * 3)
            self.proj = nn.Linear(emb, emb)
            self.dropout = nn.Dropout(0.1)
            self.register_buffer("mask", torch.tril(torch.ones(block, block, dtype=torch.bool)))

        def forward(self, x):
            b, t, c = x.shape
            q, k, v = self.qkv(x).chunk(3, dim=-1)
            q = q.view(b, t, self.heads, self.head_dim).transpose(1, 2)
            k = k.view(b, t, self.heads, self.head_dim).transpose(1, 2)
            v = v.view(b, t, self.heads, self.head_dim).transpose(1, 2)
            scores = (q @ k.transpose(-2, -1)) * (self.head_dim ** -0.5)
            scores = scores.masked_fill(~self.mask[:t, :t], float("-inf"))
            weights = self.dropout(F.softmax(scores, dim=-1))
            y = weights @ v
            y = y.transpose(1, 2).contiguous().view(b, t, c)
            return self.dropout(self.proj(y))

    class Block(nn.Module):
        def __init__(self):
            super().__init__()
            emb = config["embedding_size"]
            self.ln1 = nn.LayerNorm(emb)
            self.attn = CausalSelfAttention()
            self.ln2 = nn.LayerNorm(emb)
            self.ff = nn.Sequential(nn.Linear(emb, emb * 4), nn.GELU(), nn.Linear(emb * 4, emb), nn.Dropout(0.1))

        def forward(self, x):
            x = x + self.attn(self.ln1(x))
            return x + self.ff(self.ln2(x))

    class TinyTransformer(nn.Module):
        def __init__(self):
            super().__init__()
            emb, block = config["embedding_size"], config["block_size"]
            self.token_emb = nn.Embedding(256, emb)
            self.position_emb = nn.Embedding(block, emb)
            self.blocks = nn.Sequential(*[Block() for _ in range(config["layers"])])
            self.ln_f = nn.LayerNorm(emb)
            self.head = nn.Linear(emb, 256)
            self.apply(self._init)

        @staticmethod
        def _init(module):
            if isinstance(module, nn.Linear):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.Embedding):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)

        def forward(self, idx, targets=None):
            b, t = idx.shape
            pos = torch.arange(t, device=idx.device)
            x = self.token_emb(idx) + self.position_emb(pos)[None, :, :]
            logits = self.head(self.ln_f(self.blocks(x)))
            loss = F.cross_entropy(logits.reshape(-1, 256), targets.reshape(-1)) if targets is not None else None
            return logits, loss

    return TinyTransformer()


def train_text(jid: str, dataset_path: str, model_name: str, settings: dict):
    if not _train_lock.acquire(blocking=False):
        update_job(jid, status="failed", message="Another model is training. Wait for it to finish.")
        return
    try:
        require_torch()
        update_job(jid, status="running", progress=2, message="Reading and tokenizing text")
        text = Path(dataset_path).read_text("utf-8")
        encoded = bytearray(text.encode("utf-8"))
        data = torch.frombuffer(encoded, dtype=torch.uint8).to(dtype=torch.long)
        cfg = {
            "block_size": int(settings.get("block_size", 96)),
            "embedding_size": int(settings.get("embedding_size", 64)),
            "attention_heads": int(settings.get("attention_heads", 4)),
            "layers": int(settings.get("layers", 2)),
            "batch_size": int(settings.get("batch_size", 8)),
            "epochs": int(settings.get("epochs", 3)),
            "steps_per_epoch": int(settings.get("steps_per_epoch", 30)),
            "learning_rate": float(settings.get("learning_rate", 0.0003)),
        }
        if cfg["embedding_size"] % cfg["attention_heads"]:
            raise ValueError("Embedding size must be divisible by attention heads")
        if not (32 <= cfg["block_size"] <= 512 and 32 <= cfg["embedding_size"] <= 256):
            raise ValueError("Block size must be 32-512 and embedding size 32-256")
        if not (1 <= cfg["layers"] <= 6 and 1 <= cfg["attention_heads"] <= 8):
            raise ValueError("Use 1-6 layers and 1-8 attention heads")
        if not (1 <= cfg["epochs"] <= 20 and 5 <= cfg["steps_per_epoch"] <= 150):
            raise ValueError("Epochs must be 1-20 and steps per epoch 5-150")
        if len(data) < cfg["block_size"] * 2 + 2:
            raise ValueError(f"Dataset is too short for block size {cfg['block_size']}. Add more text or lower block size.")

        split = max(cfg["block_size"] + 1, int(len(data) * 0.9))
        split = min(split, len(data) - cfg["block_size"] - 1)
        train_data, val_data = data[:split], data[split:]
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = _text_model(cfg).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=cfg["learning_rate"])

        def batch(source):
            max_start = len(source) - cfg["block_size"] - 1
            starts = torch.randint(max_start + 1, (cfg["batch_size"],))
            x = torch.stack([source[int(i):int(i) + cfg["block_size"]] for i in starts])
            y = torch.stack([source[int(i) + 1:int(i) + cfg["block_size"] + 1] for i in starts])
            return x.to(device), y.to(device)

        history = []
        total_steps = cfg["epochs"] * cfg["steps_per_epoch"]
        step = 0
        for epoch in range(cfg["epochs"]):
            model.train()
            losses = []
            for _ in range(cfg["steps_per_epoch"]):
                xb, yb = batch(train_data)
                _, loss = model(xb, yb)
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                losses.append(float(loss.detach().cpu()))
                step += 1
            model.eval()
            with torch.no_grad():
                vx, vy = batch(val_data)
                _, vloss = model(vx, vy)
            record = {"epoch": epoch + 1, "train_loss": round(sum(losses) / len(losses), 4),
                      "validation_loss": round(float(vloss.cpu()), 4)}
            history.append(record)
            update_job(jid, progress=int(10 + 85 * step / total_steps), message=f"Finished epoch {epoch + 1}/{cfg['epochs']}", metrics=history)

        name = slug(model_name)
        dest = text_model_dir(name)
        dest.mkdir(parents=True, exist_ok=True)
        checkpoint = {"config": cfg, "state_dict": model.cpu().state_dict(), "format": "axl-byte-transformer-v1"}
        torch.save(checkpoint, dest / "model.pt")
        metadata = {"name": name, "kind": "text", "architecture": "decoder-only Transformer", "tokenizer": "UTF-8 byte-level (256 tokens)",
                    "dataset_bytes": len(data), "parameters": sum(p.numel() for p in model.parameters()),
                    "trained_at": int(time.time()), "device": str(device), "config": cfg, "metrics": history,
                    "warning": "Experimental small language model; quality depends heavily on dataset size and format."}
        (dest / "metadata.json").write_text(json.dumps(metadata, indent=2), "utf-8")
        update_job(jid, status="completed", progress=100, message="Training complete. Model saved.", model=name, metrics=history)
    except Exception as exc:
        update_job(jid, status="failed", message=f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        try:
            Path(dataset_path).unlink(missing_ok=True)
        except Exception:
            pass
        _train_lock.release()


def _load_text_model(name: str, device=None):
    require_torch()
    folder = text_model_dir(name)
    path = folder / "model.pt"
    if not path.exists():
        raise FileNotFoundError(f"No trained text model named '{slug(name)}'")
    device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    try:
        checkpoint = torch.load(path, map_location=device, weights_only=True)
    except TypeError:  # Older PyTorch
        checkpoint = torch.load(path, map_location=device)
    model = _text_model(checkpoint["config"]).to(device)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model, checkpoint["config"], device


def generate_text(name: str, prompt: str, max_new_tokens: int = 160, temperature: float = 0.8):
    if not prompt.strip():
        raise ValueError("Enter a prompt")
    if len(prompt) > 3000:
        raise ValueError("Prompt is limited to 3,000 characters")
    max_new_tokens = max(1, min(int(max_new_tokens), 400))
    temperature = max(0.1, min(float(temperature), 1.5))
    model, cfg, device = _load_text_model(name)
    encoded = list(prompt.encode("utf-8"))
    idx = torch.tensor([encoded[-cfg["block_size"]:]], dtype=torch.long, device=device)
    with torch.no_grad():
        for _ in range(max_new_tokens):
            ctx = idx[:, -cfg["block_size"]:]
            logits, _ = model(ctx)
            logits = logits[:, -1, :] / temperature
            probs = torch.softmax(logits, dim=-1)
            next_token = torch.multinomial(probs, num_samples=1)
            idx = torch.cat((idx, next_token), dim=1)
    raw = bytes(idx[0].tolist())
    prefix_len = len(encoded[-cfg["block_size"]:])
    continuation = raw[prefix_len:].decode("utf-8", errors="ignore")
    output = prompt + continuation
    return {"model": slug(name), "text": output, "continuation": continuation,
            "generated_bytes": max(0, len(raw) - prefix_len),
            "warning": "This is a tiny experimental next-token model, not a reliable factual assistant."}


def _vision_model(num_classes: int):
    require_torch()
    nn = torch.nn
    return nn.Sequential(
        nn.Conv2d(3, 24, 3, padding=1), nn.BatchNorm2d(24), nn.ReLU(inplace=True), nn.MaxPool2d(2),
        nn.Conv2d(24, 48, 3, padding=1), nn.BatchNorm2d(48), nn.ReLU(inplace=True), nn.MaxPool2d(2),
        nn.Conv2d(48, 96, 3, padding=1), nn.BatchNorm2d(96), nn.ReLU(inplace=True), nn.AdaptiveAvgPool2d((1, 1)),
        nn.Flatten(), nn.Dropout(0.2), nn.Linear(96, num_classes)
    )


def train_vision(jid: str, dataset_root: str, model_name: str, epochs: int = 5, image_size: int = 96, batch_size: int = 16):
    if not _train_lock.acquire(blocking=False):
        update_job(jid, status="failed", message="Another model is training. Wait for it to finish.")
        return
    try:
        require_torch()
        from torchvision import datasets, transforms
        from torch.utils.data import DataLoader, Subset
        update_job(jid, status="running", progress=3, message="Indexing image folders")
        root = Path(dataset_root)
        classes = sorted(p.name for p in root.iterdir() if p.is_dir())
        if len(classes) < 2:
            raise ValueError("Dataset must contain at least two class folders")
        counts = {}
        for cls in classes:
            counts[cls] = sum(1 for p in (root / cls).rglob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"})
        if any(n < 2 for n in counts.values()):
            raise ValueError("Each class folder needs at least 2 valid images; 30-100+ per class is a better starting point")
        epochs = max(1, min(int(epochs), 20))
        image_size = max(64, min(int(image_size), 160))
        batch_size = max(2, min(int(batch_size), 32))
        train_tf = transforms.Compose([transforms.Resize((image_size, image_size)), transforms.RandomHorizontalFlip(),
                                       transforms.RandomRotation(8), transforms.ToTensor(),
                                       transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
        val_tf = transforms.Compose([transforms.Resize((image_size, image_size)), transforms.ToTensor(),
                                     transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
        base = datasets.ImageFolder(str(root))
        if len(base) < 8:
            raise ValueError("Add more images; at least 8 total are required for a basic train/validation split")
        indices = torch.randperm(len(base)).tolist()
        val_count = max(2, int(len(indices) * 0.2))
        train_ids, val_ids = indices[val_count:], indices[:val_count]
        train_ds = Subset(datasets.ImageFolder(str(root), transform=train_tf), train_ids)
        val_ds = Subset(datasets.ImageFolder(str(root), transform=val_tf), val_ids)
        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
        val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = _vision_model(len(classes)).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
        criterion = torch.nn.CrossEntropyLoss()
        history = []
        for epoch in range(epochs):
            model.train(); train_loss = 0.0; seen = 0
            for images, labels in train_loader:
                images, labels = images.to(device), labels.to(device)
                optimizer.zero_grad(set_to_none=True)
                logits = model(images); loss = criterion(logits, labels)
                loss.backward(); optimizer.step()
                train_loss += float(loss.detach().cpu()) * len(labels); seen += len(labels)
            model.eval(); correct = 0; total = 0
            with torch.no_grad():
                for images, labels in val_loader:
                    logits = model(images.to(device))
                    correct += int((logits.argmax(1).cpu() == labels).sum())
                    total += len(labels)
            record = {"epoch": epoch + 1, "train_loss": round(train_loss / max(1, seen), 4),
                      "validation_accuracy": round(correct / max(1, total), 4)}
            history.append(record)
            update_job(jid, progress=int(10 + 85 * (epoch + 1) / epochs), message=f"Finished epoch {epoch + 1}/{epochs}", metrics=history)
        name = slug(model_name)
        dest = vision_model_dir(name); dest.mkdir(parents=True, exist_ok=True)
        torch.save({"state_dict": model.cpu().state_dict(), "classes": classes, "image_size": image_size,
                    "format": "axl-small-cnn-v1"}, dest / "model.pt")
        metadata = {"name": name, "kind": "vision", "architecture": "small CNN trained from scratch",
                    "classes": classes, "image_count": len(base), "trained_at": int(time.time()),
                    "device": str(device), "image_size": image_size, "metrics": history,
                    "warning": "Predictions are experimental. Validate on separate real-world images before relying on them."}
        (dest / "metadata.json").write_text(json.dumps(metadata, indent=2), "utf-8")
        update_job(jid, status="completed", progress=100, message="Vision training complete. Model saved.", model=name, metrics=history)
    except Exception as exc:
        update_job(jid, status="failed", message=f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        _train_lock.release()


def predict_vision(name: str, image_bytes: bytes):
    require_torch()
    from PIL import Image
    from torchvision import transforms
    folder = vision_model_dir(name)
    path = folder / "model.pt"
    if not path.exists():
        raise FileNotFoundError(f"No trained vision model named '{slug(name)}'")
    try:
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        checkpoint = torch.load(path, map_location="cpu")
    classes = checkpoint["classes"]
    image_size = int(checkpoint["image_size"])
    model = _vision_model(len(classes))
    model.load_state_dict(checkpoint["state_dict"]); model.eval()
    tf = transforms.Compose([transforms.Resize((image_size, image_size)), transforms.ToTensor(),
                             transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
    try:
        import io
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        raise ValueError("Couldn't read the uploaded image. Use JPG, PNG or WEBP")
    x = tf(image).unsqueeze(0)
    with torch.no_grad():
        probs = torch.softmax(model(x), dim=1)[0]
    idx = int(torch.argmax(probs))
    return {"model": slug(name), "category": classes[idx], "confidence": round(float(probs[idx]), 4),
            "scores": {classes[i]: round(float(probs[i]), 4) for i in range(len(classes))},
            "warning": "Experimental prediction. Check the item manually, especially if it is mixed, dirty or hazardous."}
