/*
 * AXL Local AI runtime
 * Training, inference and model storage stay inside this browser.
 * TensorFlow.js uses WebGL when available (device GPU) and falls back to CPU.
 * AI Lab datasets, test images, model-training inputs and model weights stay in this browser.
 * Studio rule evaluation and the existing API services remain server-backed.
 */
(function (global) {
  "use strict";

  const DB_NAME = "axl-browser-ai";
  const DB_VERSION = 1;
  const STORE = "models";
  let dbPromise;
  let runtimePromise;

  const openDb = () => {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: "id" });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("Could not open browser model storage."));
      request.onblocked = () => reject(new Error("Browser model storage is blocked by another tab. Close other AXL tabs and retry."));
    });
    return dbPromise;
  };

  async function dbGetAll() {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }

  async function dbPut(record) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(record);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error || new Error("Could not save the model in this browser."));
      tx.onabort = () => reject(tx.error || new Error("Model save was interrupted."));
    });
  }

  async function dbGet(id) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).get(id);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
  }

  async function dbDelete(id) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(id);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  function requireRuntime() {
    if (!global.tf) throw new Error("TensorFlow.js is not loaded yet. Open AI Lab and let the browser runtime initialize.");
    if (!global.JSZip) throw new Error("The ZIP reader did not load. Refresh this page and try again.");
    return global.tf;
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[data-axl-runtime="${src}"]`);
      if (existing) {
        if (existing.dataset.loaded === "yes") return resolve();
        existing.addEventListener("load", resolve, { once: true });
        existing.addEventListener("error", () => reject(new Error("Could not load TensorFlow.js from its CDN.")), { once: true });
        return;
      }
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.dataset.axlRuntime = src;
      script.onload = () => { script.dataset.loaded = "yes"; resolve(); };
      script.onerror = () => { script.remove(); reject(new Error("Could not load TensorFlow.js from its CDN.")); };
      document.head.appendChild(script);
    });
  }

  async function ensureRuntime() {
    if (!global.tf) {
      try {
        await loadScript("https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.22.0/dist/tf.min.js");
      } catch (_) {
        await loadScript("https://unpkg.com/@tensorflow/tfjs@4.22.0/dist/tf.min.js");
      }
    }
    return requireRuntime();
  }

  async function init() {
    if (runtimePromise) return runtimePromise;
    runtimePromise = (async () => {
      const tf = await ensureRuntime();
      let backend = "";
      try {
        const webglOk = await tf.setBackend("webgl");
        if (webglOk) backend = "webgl";
      } catch (_) { /* CPU fallback below */ }
      if (!backend) {
        try {
          const cpuOk = await tf.setBackend("cpu");
          if (cpuOk) backend = "cpu";
        } catch (_) { /* report below */ }
      }
      await tf.ready();
      backend = tf.getBackend() || backend || "unknown";
      const webgl = backend === "webgl" || backend === "webgpu";
      return {
        version: tf.version && tf.version.tfjs ? tf.version.tfjs : "loaded",
        backend,
        device: webgl ? "Browser WebGL runtime" : "Browser CPU runtime",
        note: webgl ? "WebGL can use the phone GPU when browser/device hardware acceleration is available; otherwise the browser may software-render it." : "WebGL is unavailable; TensorFlow.js is using the phone CPU."
      };
    })();
    try { return await runtimePromise; }
    catch (err) { runtimePromise = null; throw err; }
  }

  function slug(value) {
    const name = String(value || "").trim().replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "").slice(0, 48);
    if (!name) throw new Error("Enter a model name using letters, numbers, hyphens or underscores.");
    return name;
  }

  function shuffle(items) {
    const a = items.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function createTransformer(config) {
    const tf = requireRuntime();
    const { blockSize, embeddingSize: d, heads, layers } = config;
    if (d % heads !== 0) throw new Error("Embedding size must be divisible by attention heads.");
    const headDim = d / heads;
    const vars = [];
    const variable = (shape, init = "normal") => {
      let initial;
      if (init === "ones") initial = tf.ones(shape);
      else if (init === "zeros") initial = tf.zeros(shape);
      else initial = tf.randomNormal(shape, 0, 0.02);
      const v = tf.variable(initial);
      initial.dispose();
      vars.push(v);
      return v;
    };
    const tokenEmb = variable([256, d]);
    const positionEmb = variable([blockSize, d]);
    const blocks = [];
    for (let i = 0; i < layers; i++) {
      blocks.push({
        ln1Scale: variable([d], "ones"), ln1Bias: variable([d], "zeros"),
        qkvW: variable([d, d * 3]), qkvB: variable([d * 3], "zeros"),
        projW: variable([d, d]), projB: variable([d], "zeros"),
        ln2Scale: variable([d], "ones"), ln2Bias: variable([d], "zeros"),
        ff1W: variable([d, d * 4]), ff1B: variable([d * 4], "zeros"),
        ff2W: variable([d * 4, d]), ff2B: variable([d], "zeros")
      });
    }
    const finalScale = variable([d], "ones");
    const finalBias = variable([d], "zeros");
    const headW = variable([d, 256]);
    const headB = variable([256], "zeros");

    function norm(x, scale, bias) {
      const mean = tf.mean(x, -1, true);
      const variance = tf.mean(tf.square(x.sub(mean)), -1, true);
      return x.sub(mean).div(tf.sqrt(variance.add(1e-5))).mul(scale).add(bias);
    }

    function forward(ids) {
      const [batch, time] = ids.shape;
      if (time > blockSize) throw new Error("Input is longer than this model's context window.");
      let x = tf.gather(tokenEmb, ids).add(positionEmb.slice([0, 0], [time, d]).expandDims(0));
      for (const b of blocks) {
        const n1 = norm(x, b.ln1Scale, b.ln1Bias);
        const qkv = n1.matMul(b.qkvW).add(b.qkvB).reshape([batch, time, 3, heads, headDim]);
        const pieces = tf.split(qkv, 3, 2);
        const q = pieces[0].squeeze([2]).transpose([0, 2, 1, 3]);
        const k = pieces[1].squeeze([2]).transpose([0, 2, 1, 3]);
        const v = pieces[2].squeeze([2]).transpose([0, 2, 1, 3]);
        const maskData = new Float32Array(time * time);
        for (let row = 0; row < time; row++) for (let col = 0; col <= row; col++) maskData[row * time + col] = 1;
        const mask = tf.tensor2d(maskData, [time, time]).greater(0).reshape([1, 1, time, time]).tile([batch, heads, 1, 1]);
        const scores = q.matMul(k.transpose([0, 1, 3, 2])).mul(1 / Math.sqrt(headDim));
        const masked = tf.where(mask, scores, tf.fill(scores.shape, -1e9));
        const attention = tf.softmax(masked, -1).matMul(v).transpose([0, 2, 1, 3]).reshape([batch, time, d]);
        x = x.add(attention.matMul(b.projW).add(b.projB));
        const n2 = norm(x, b.ln2Scale, b.ln2Bias);
        const ff = tf.relu(n2.matMul(b.ff1W).add(b.ff1B)).matMul(b.ff2W).add(b.ff2B);
        x = x.add(ff);
      }
      return norm(x, finalScale, finalBias).matMul(headW).add(headB);
    }

    return {
      vars,
      forward,
      dispose() { vars.forEach(v => v.dispose()); }
    };
  }

  async function trainText({ file, name, epochs, steps, blockSize, embeddingSize, heads, layers, onProgress }) {
    const tf = await ensureRuntime();
    await init();
    if (!file) throw new Error("Choose a text dataset file first.");
    if (file.size > 3 * 1024 * 1024) throw new Error("For phone training, keep text datasets under 3 MB.");
    const modelName = slug(name);
    const id = `text:${modelName.toLowerCase()}`;
    if (await dbGet(id)) throw new Error("A text model with that name already exists. Delete it first or choose another name.");
    const raw = (await file.text()).trim();
    const bytes = new TextEncoder().encode(raw);
    blockSize = Math.max(32, Math.min(128, Number(blockSize) || 48));
    embeddingSize = Math.max(16, Math.min(96, Number(embeddingSize) || 32));
    heads = Math.max(1, Math.min(4, Number(heads) || 2));
    layers = Math.max(1, Math.min(2, Number(layers) || 1));
    epochs = Math.max(1, Math.min(10, Number(epochs) || 2));
    steps = Math.max(5, Math.min(60, Number(steps) || 15));
    if (embeddingSize % heads) throw new Error("Embedding size must be divisible by the number of attention heads.");
    if (bytes.length < blockSize * 2 + 2) throw new Error(`Dataset is too short. Add at least ${blockSize * 2 + 2} UTF-8 bytes or reduce the context size.`);

    const split = Math.max(blockSize + 1, Math.min(bytes.length - blockSize - 1, Math.floor(bytes.length * 0.9)));
    const trainData = bytes.slice(0, split);
    const valData = bytes.slice(split);
    const cfg = { blockSize, embeddingSize, heads, layers };
    const model = createTransformer(cfg);
    const optimizer = tf.train.adam(0.001);
    const batchSize = 2;
    const makeBatch = source => {
      const xs = new Int32Array(batchSize * blockSize);
      const ys = new Int32Array(batchSize * blockSize);
      const maxStart = source.length - blockSize - 1;
      for (let b = 0; b < batchSize; b++) {
        const start = Math.floor(Math.random() * (maxStart + 1));
        for (let t = 0; t < blockSize; t++) {
          xs[b * blockSize + t] = source[start + t];
          ys[b * blockSize + t] = source[start + t + 1];
        }
      }
      return {
        x: tf.tensor2d(xs, [batchSize, blockSize], "int32"),
        y: tf.tensor2d(ys, [batchSize, blockSize], "int32")
      };
    };
    const lossFor = (x, y) => {
      const logits = model.forward(x).reshape([-1, 256]);
      const labels = tf.oneHot(y.reshape([-1]), 256);
      return tf.neg(tf.mean(tf.sum(tf.mul(labels, tf.logSoftmax(logits, 1)), 1)));
    };

    const history = [];
    try {
      for (let epoch = 0; epoch < epochs; epoch++) {
        const losses = [];
        for (let step = 0; step < steps; step++) {
          const batch = makeBatch(trainData);
          const cost = optimizer.minimize(() => tf.tidy(() => lossFor(batch.x, batch.y)), true, model.vars);
          if (!cost) throw new Error("TensorFlow.js could not calculate model gradients.");
          losses.push((await cost.data())[0]);
          cost.dispose(); batch.x.dispose(); batch.y.dispose();
          if (onProgress) onProgress({ percent: Math.round(((epoch * steps + step + 1) / (epochs * steps)) * 90), message: `Training epoch ${epoch + 1}/${epochs}, step ${step + 1}/${steps}` });
          await tf.nextFrame();
        }
        const validation = makeBatch(valData);
        const valLossTensor = tf.tidy(() => lossFor(validation.x, validation.y));
        const validationLoss = (await valLossTensor.data())[0];
        valLossTensor.dispose(); validation.x.dispose(); validation.y.dispose();
        history.push({ epoch: epoch + 1, train_loss: Number((losses.reduce((a, b) => a + b, 0) / losses.length).toFixed(4)), validation_loss: Number(validationLoss.toFixed(4)) });
        if (onProgress) onProgress({ percent: Math.round(((epoch + 1) / epochs) * 95), message: `Finished epoch ${epoch + 1}/${epochs}`, metrics: history.slice() });
      }

      const named = {};
      model.vars.forEach((v, i) => { named[`w${i}`] = v; });
      const encoded = await tf.io.encodeWeights(named);
      const params = model.vars.reduce((sum, v) => sum + v.size, 0);
      await dbPut({
        id, type: "text", name: modelName, createdAt: Date.now(),
        meta: { architecture: "Decoder-only causal Transformer", tokenizer: "UTF-8 byte-level (256 tokens)", parameters: params, datasetBytes: bytes.length, config: cfg, metrics: history },
        weightSpecs: encoded.specs, weightData: encoded.data
      });
      if (onProgress) onProgress({ percent: 100, message: "Training complete. Model saved in this browser.", metrics: history });
      return { id, name: modelName, metrics: history };
    } finally {
      optimizer.dispose();
      model.dispose();
    }
  }

  async function loadTextModel(id) {
    const tf = requireRuntime();
    const record = await dbGet(id);
    if (!record || record.type !== "text") throw new Error("The selected text model was not found in this browser.");
    const model = createTransformer(record.meta.config);
    const decoded = await tf.io.decodeWeights(record.weightData, record.weightSpecs);
    try {
      model.vars.forEach((v, i) => {
        const source = decoded[`w${i}`];
        if (!source) throw new Error("A saved model weight is missing. The model may be damaged.");
        v.assign(source);
      });
    } finally {
      Object.values(decoded).forEach(t => t.dispose());
    }
    return { model, record };
  }

  async function generateText(id, prompt, maxTokens = 100, temperature = 0.8) {
    const tf = await ensureRuntime();
    await init();
    if (!String(prompt || "").trim()) throw new Error("Enter a prompt first.");
    if (prompt.length > 1800) throw new Error("Keep the prompt under 1,800 characters on mobile.");
    const { model, record } = await loadTextModel(id);
    const block = record.meta.config.blockSize;
    const inputBytes = Array.from(new TextEncoder().encode(prompt));
    const generated = [];
    maxTokens = Math.max(1, Math.min(120, Number(maxTokens) || 80));
    temperature = Math.max(0.15, Math.min(1.5, Number(temperature) || 0.8));
    try {
      for (let i = 0; i < maxTokens; i++) {
        const context = inputBytes.concat(generated).slice(-block);
        const ids = tf.tensor2d(context, [1, context.length], "int32");
        const logits = tf.tidy(() => model.forward(ids).slice([0, context.length - 1, 0], [1, 1, 256]).reshape([256]).div(temperature));
        ids.dispose();
        const probabilitiesTensor = tf.tidy(() => tf.softmax(logits));
        logits.dispose();
        const probabilities = await probabilitiesTensor.data();
        probabilitiesTensor.dispose();
        let pick = Math.random();
        let token = 32;
        for (let j = 0; j < probabilities.length; j++) {
          pick -= probabilities[j];
          if (pick <= 0) { token = j; break; }
        }
        generated.push(token);
        if (i % 4 === 3) await tf.nextFrame();
      }
      const continuation = new TextDecoder("utf-8", { fatal: false }).decode(new Uint8Array(generated)).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "");
      return { text: `${prompt}${continuation}`, continuation, warning: "Small experimental Transformer trained from your dataset; it can produce incorrect or incoherent text and is not a factual assistant." };
    } finally {
      model.dispose();
    }
  }

  function createVisionModel(imageSize, classCount) {
    const tf = requireRuntime();
    const model = tf.sequential();
    model.add(tf.layers.conv2d({ inputShape: [imageSize, imageSize, 3], filters: 12, kernelSize: 3, padding: "same", activation: "relu" }));
    model.add(tf.layers.maxPooling2d({ poolSize: [2, 2] }));
    model.add(tf.layers.conv2d({ filters: 24, kernelSize: 3, padding: "same", activation: "relu" }));
    model.add(tf.layers.maxPooling2d({ poolSize: [2, 2] }));
    model.add(tf.layers.conv2d({ filters: 32, kernelSize: 3, padding: "same", activation: "relu" }));
    model.add(tf.layers.globalAveragePooling2d());
    model.add(tf.layers.dense({ units: 32, activation: "relu" }));
    model.add(tf.layers.dropout({ rate: 0.2 }));
    model.add(tf.layers.dense({ units: classCount, activation: "softmax" }));
    model.compile({ optimizer: tf.train.adam(0.001), loss: "categoricalCrossentropy", metrics: ["accuracy"] });
    return model;
  }

  async function blobToPixels(blob, imageSize) {
    const tf = requireRuntime();
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.decoding = "async";
    try {
      img.src = url;
      if (img.decode) await img.decode();
      else await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = () => reject(new Error("Could not decode an image in the dataset.")); });
      const tensor = tf.tidy(() => tf.browser.fromPixels(img, 3).resizeBilinear([imageSize, imageSize]).toFloat().div(255));
      try { return new Float32Array(await tensor.data()); }
      finally { tensor.dispose(); }
    } finally {
      URL.revokeObjectURL(url);
      img.src = "";
    }
  }

  async function trainVision({ file, name, epochs, imageSize, batchSize, onProgress }) {
    const tf = await ensureRuntime();
    await init();
    if (!file) throw new Error("Choose a ZIP image dataset first.");
    if (file.size > 45 * 1024 * 1024) throw new Error("For mobile training, keep the ZIP dataset under 45 MB.");
    const modelName = slug(name);
    const metaId = `vision:${modelName.toLowerCase()}`;
    if (await dbGet(metaId)) throw new Error("A vision model with that name already exists. Delete it first or choose another name.");
    imageSize = Math.max(48, Math.min(128, Number(imageSize) || 64));
    epochs = Math.max(1, Math.min(12, Number(epochs) || 4));
    batchSize = Math.max(2, Math.min(16, Number(batchSize) || 4));

    if (onProgress) onProgress({ percent: 1, message: "Reading the ZIP dataset in this browser…" });
    const zip = await global.JSZip.loadAsync(file, { checkCRC32: false });
    let entries = Object.values(zip.files).filter(entry => !entry.dir && /\.(jpe?g|png|webp|bmp)$/i.test(entry.name));
    if (entries.length > 400) throw new Error("This phone-first trainer is capped at 400 images per model. Start with a smaller, balanced dataset.");
    if (entries.length < 8) throw new Error("ZIP needs at least 8 images arranged in class folders.");
    const firstParts = entries.map(e => e.name.split("/").filter(Boolean));
    const roots = new Set(firstParts.map(parts => parts[0]));
    const wrapped = roots.size === 1 && firstParts.every(parts => parts.length >= 3);
    const classes = new Map();
    entries.forEach((entry, i) => {
      const parts = firstParts[i];
      const className = parts[wrapped ? 1 : 0];
      if (!className || className.startsWith(".")) return;
      if (!classes.has(className)) classes.set(className, []);
      classes.get(className).push(entry);
    });
    const classNames = [...classes.keys()].sort();
    if (classNames.length < 2 || classNames.length > 12) throw new Error("Use 2–12 class folders, for example plastic/, paper/ and metal/.");
    for (const c of classNames) if (classes.get(c).length < 2) throw new Error(`Class '${c}' needs at least 2 images. Aim for 20–50 varied photos per class.`);

    const trainItems = [], valItems = [];
    let processed = 0;
    const allByClass = [];
    for (let label = 0; label < classNames.length; label++) {
      const classEntries = shuffle(classes.get(classNames[label]));
      const valCount = Math.max(1, Math.floor(classEntries.length * 0.2));
      classEntries.forEach((entry, index) => allByClass.push({ entry, label, split: index < valCount ? "val" : "train" }));
    }

    const trainImages = [], trainLabels = [], valImages = [], valLabels = [];
    try {
      for (const item of allByClass) {
        const blob = await item.entry.async("blob");
        const pixels = await blobToPixels(blob, imageSize);
        if (item.split === "train") { trainImages.push(pixels); trainLabels.push(item.label); }
        else { valImages.push(pixels); valLabels.push(item.label); }
        processed++;
        if (onProgress && processed % 4 === 0) onProgress({ percent: 5 + Math.round((processed / allByClass.length) * 25), message: `Decoded ${processed}/${allByClass.length} images locally…` });
        if (processed % 8 === 0) await tf.nextFrame();
      }
      if (trainImages.length < classNames.length * 1 || valImages.length < classNames.length) throw new Error("Not enough images for a train/validation split. Add more images per class.");

      const flatten = arrays => {
        const length = arrays.length * imageSize * imageSize * 3;
        const joined = new Float32Array(length);
        let offset = 0;
        for (const array of arrays) { joined.set(array, offset); offset += array.length; }
        return joined;
      };
      const xTrain = tf.tensor4d(flatten(trainImages), [trainImages.length, imageSize, imageSize, 3]);
      const yTrainIds = tf.tensor1d(trainLabels, "int32");
      const yTrain = tf.oneHot(yTrainIds, classNames.length);
      const xVal = tf.tensor4d(flatten(valImages), [valImages.length, imageSize, imageSize, 3]);
      const yValIds = tf.tensor1d(valLabels, "int32");
      const yVal = tf.oneHot(yValIds, classNames.length);
      yTrainIds.dispose(); yValIds.dispose();
      const model = createVisionModel(imageSize, classNames.length);
      const history = [];
      try {
        if (onProgress) onProgress({ percent: 32, message: `Training a small CNN on ${trainImages.length} images using this device…` });
        const fit = await model.fit(xTrain, yTrain, {
          epochs, batchSize, shuffle: true, validationData: [xVal, yVal],
          callbacks: {
            onEpochEnd: async (epoch, logs) => {
              const row = { epoch: epoch + 1, train_loss: Number((logs.loss || 0).toFixed(4)), validation_accuracy: Number((logs.val_accuracy || 0).toFixed(4)), validation_loss: Number((logs.val_loss || 0).toFixed(4)) };
              history.push(row);
              if (onProgress) onProgress({ percent: 35 + Math.round(((epoch + 1) / epochs) * 55), message: `Finished vision epoch ${epoch + 1}/${epochs}`, metrics: history.slice() });
              await tf.nextFrame();
            }
          }
        });
        const modelPath = `axl-vision-${modelName.toLowerCase()}-${Date.now()}`;
        await model.save(`indexeddb://${modelPath}`);
        await dbPut({
          id: metaId, type: "vision", name: modelName, modelPath, createdAt: Date.now(),
          meta: { architecture: "Small CNN classifier", classes: classNames, imageCount: allByClass.length, imageSize, metrics: history, validationAccuracy: fit.history.val_accuracy ? Number(fit.history.val_accuracy[fit.history.val_accuracy.length - 1]) : null }
        });
        if (onProgress) onProgress({ percent: 100, message: "Vision model saved in this browser.", metrics: history });
        return { id: metaId, name: modelName, metrics: history };
      } finally {
        model.dispose(); xTrain.dispose(); yTrain.dispose(); xVal.dispose(); yVal.dispose();
      }
    } catch (err) {
      throw err;
    }
  }

  async function predictVision(id, file) {
    const tf = await ensureRuntime();
    await init();
    if (!file) throw new Error("Choose an image to classify.");
    if (file.size > 12 * 1024 * 1024) throw new Error("Keep test images under 12 MB.");
    const record = await dbGet(id);
    if (!record || record.type !== "vision") throw new Error("The selected vision model was not found in this browser.");
    const model = await tf.loadLayersModel(`indexeddb://${record.modelPath}`);
    try {
      const pixels = await blobToPixels(file, record.meta.imageSize);
      const input = tf.tensor4d(pixels, [1, record.meta.imageSize, record.meta.imageSize, 3]);
      const prediction = model.predict(input);
      try {
        const scoresArray = await prediction.data();
        const scores = Array.from(scoresArray).map((score, index) => ({ category: record.meta.classes[index], confidence: Number(score.toFixed(5)) })).sort((a, b) => b.confidence - a.confidence);
        return { category: scores[0].category, confidence: scores[0].confidence, scores, warning: "This model only recognizes the classes and visual patterns learned from its own training dataset." };
      } finally { input.dispose(); prediction.dispose(); }
    } finally { model.dispose(); }
  }

  async function listModels() {
    const records = await dbGetAll();
    return records.map(record => ({
      id: record.id, name: record.name, kind: record.type,
      ...(record.meta || {}), createdAt: record.createdAt,
      ...(record.type === "text" ? { parameters: record.meta.parameters, datasetBytes: record.meta.datasetBytes } : {})
    })).sort((a, b) => b.createdAt - a.createdAt);
  }

  async function deleteModel(id) {
    const record = await dbGet(id);
    if (!record) return;
    if (record.type === "vision" && record.modelPath) {
      try { await requireRuntime().io.removeModel(`indexeddb://${record.modelPath}`); }
      catch (err) { console.warn("Could not remove the TensorFlow model payload:", err); }
    }
    await dbDelete(id);
  }

  global.AXLAI = {
    init, listModels, trainText, generateText, trainVision, predictVision, deleteModel,
    get backend() { return global.tf ? global.tf.getBackend() : "not loaded"; }
  };
})(window);
