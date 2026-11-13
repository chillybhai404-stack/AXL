/* AXL code editor: syntax colours, line numbers, auto-closing quotes/brackets. */
const KW_CTRL = new Set(["when", "asked", "about", "anything", "if", "then", "where", "and", "or", "not", "import", "unsure"]);
const KW_DECL = new Set(["assistant", "instructions", "let", "table", "row", "use", "fallback", "vault", "model"]);
const KW_ACT = new Set(["say", "find", "in", "sql", "search", "math", "llm"]);
const FUNCS = new Set(["sqrt", "abs", "round", "floor", "ceil", "min", "max", "len", "upper", "lower", "sin", "cos", "tan", "log", "log10"]);
const ADDON_NAMES = new Set(["math", "weather", "dictionary", "countries", "currency", "books", "joke", "wikipedia"]);
const TOKEN = /(#.*)|("(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)|(->|==|!=|>=|<=|\*\*|[-+*\/^%=<>])|([()\[\]{},:])|(\s+)|(.)/g;

function escHtml(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

function highlightLine(line) {
  let out = "", prev = "", m;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(line)) !== null) {
    const t = m[0];
    if (m[1]) out += `<span class="c">${escHtml(t)}</span>`;
    else if (m[2]) out += `<span class="s">${escHtml(t).replace(/\{[^{}]*\}/g, x => `<span class="i">${x}</span>`)}</span>`;
    else if (m[3]) out += `<span class="n">${t}</span>`;
    else if (m[4]) {
      const w = t.toLowerCase();
      let cls = "v";
      if (prev === "use" && ADDON_NAMES.has(w)) cls = "a";
      else if (KW_CTRL.has(w)) cls = "k";
      else if (KW_DECL.has(w)) cls = "d";
      else if (KW_ACT.has(w)) cls = "f";
      else if (FUNCS.has(w) && line[TOKEN.lastIndex] === "(") cls = "f";
      out += `<span class="${cls}">${t}</span>`;
      prev = w;
      continue;
    } else if (m[5]) out += `<span class="${t === "->" ? "k" : "o"}">${escHtml(t)}</span>`;
    else out += escHtml(t);
    if (!m[7]) prev = "";
  }
  return out;
}

function highlight(src) { return src.split("\n").map(highlightLine).join("\n") + "\n"; }

const PAIRS = {'"': '"', "'": "'", "(": ")", "[": "]", "{": "}"};
const CLOSERS = new Set([")", "]", "}"]);

class Editor {
  constructor(o) {
    this.o = o; this.ta = o.ta; this.hl = o.hl; this.gutter = o.gutter; this.errors = new Set();
    this.ta.addEventListener("input", () => { this.render(); this.o.onChange && this.o.onChange(); });
    this.ta.addEventListener("scroll", () => this.sync());
    this.ta.addEventListener("keydown", e => this.onKey(e));
    ["keyup", "click"].forEach(ev => this.ta.addEventListener(ev, () => this.cursor()));
  }
  getValue() { return this.ta.value; }
  setValue(v, keepScroll) {
    const st = this.ta.scrollTop;
    this.ta.value = v;
    this.render();
    this.ta.scrollTop = keepScroll ? st : 0;
    this.sync(); this.cursor();
  }
  setErrors(lines) { this.errors = new Set(lines); this.renderGutter(); }
  render() { this.hl.innerHTML = highlight(this.ta.value); this.renderGutter(); this.sync(); }
  renderGutter() {
    const n = this.ta.value.split("\n").length;
    let h = "";
    for (let i = 1; i <= n; i++) h += `<div class="ln${this.errors.has(i) ? " err" : ""}">${i}</div>`;
    this.gutter.innerHTML = h;
  }
  sync() {
    this.hl.scrollTop = this.ta.scrollTop; this.hl.scrollLeft = this.ta.scrollLeft;
    this.gutter.scrollTop = this.ta.scrollTop;
  }
  cursor() {
    const s = this.ta.selectionStart, before = this.ta.value.slice(0, s);
    this.o.onCursor && this.o.onCursor(before.split("\n").length, s - before.lastIndexOf("\n"));
  }
  jumpTo(line) {
    const lines = this.ta.value.split("\n");
    let idx = 0;
    for (let i = 0; i < line - 1 && i < lines.length; i++) idx += lines[i].length + 1;
    this.ta.focus();
    this.ta.setSelectionRange(idx, idx + (lines[line - 1] || "").length);
    this.ta.scrollTop = Math.max(0, (line - 5) * 20);
    this.sync(); this.cursor();
  }
  insert(text, back) {
    this.ta.focus();
    if (!document.execCommand("insertText", false, text)) {
      const s = this.ta.selectionStart;
      this.ta.setRangeText(text, s, this.ta.selectionEnd, "end");
      this.ta.dispatchEvent(new Event("input"));
    }
    if (back) { const p = this.ta.selectionStart - back; this.ta.setSelectionRange(p, p); }
  }
  onKey(e) {
    const ta = this.ta, k = e.key, s = ta.selectionStart, t = ta.selectionEnd, v = ta.value;
    if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "s") { e.preventDefault(); this.o.onSave && this.o.onSave(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const prev = v[s - 1] || "", next = v[s] || "";
    if (k === "Backspace" && s === t && prev && PAIRS[prev] === next) {
      e.preventDefault(); ta.setSelectionRange(s - 1, s + 1); document.execCommand("delete"); return;
    }
    if (k === "Enter") {
      e.preventDefault();
      const ls = v.lastIndexOf("\n", s - 1) + 1;
      this.insert("\n" + /^[ \t]*/.exec(v.slice(ls, s))[0]);
      return;
    }
    if (k === "Tab") {
      e.preventDefault();
      if (e.shiftKey) {
        const ls = v.lastIndexOf("\n", s - 1) + 1;
        if (v.slice(ls, ls + 2) === "  ") { ta.setSelectionRange(ls, ls + 2); document.execCommand("delete"); }
      } else this.insert("  ");
      return;
    }
    if (CLOSERS.has(k) && s === t && next === k) { e.preventDefault(); ta.setSelectionRange(s + 1, s + 1); return; }
    if (PAIRS[k] === undefined) return;
    const isQuote = k === '"' || k === "'";
    if (s !== t) { e.preventDefault(); this.insert(k + v.slice(s, t) + PAIRS[k]); return; }   // wrap selection
    if (isQuote && next === k) { e.preventDefault(); ta.setSelectionRange(s + 1, s + 1); return; }   // step over closing quote
    if (k === "'" && /\w/.test(prev)) return;   // apostrophe, as in don't
    if (k === '"') {
      const ls = v.lastIndexOf("\n", s - 1) + 1;
      if ((v.slice(ls, s).match(/"/g) || []).length % 2 === 1) return;   // already inside a string: this closes it
    }
    if (next && /\w/.test(next)) return;   // don't pair in front of a word
    e.preventDefault();
    this.insert(k + PAIRS[k], 1);
  }
}

if (typeof module !== "undefined") module.exports = {highlight, highlightLine};
