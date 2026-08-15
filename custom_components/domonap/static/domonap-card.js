const CARD_CSS = `
:host { display:block; }
.wrap { font-family: var(--ha-font-family, Roboto, sans-serif); color: var(--primary-text-color); }
h2 { margin: 0 0 8px; font-size: 1.25rem; }
.hint { opacity:.75; font-size:.9rem; margin: 0 0 12px; line-height:1.35; }
.row { display:flex; gap:8px; flex-wrap:wrap; margin: 8px 0; }
button, .filebtn { appearance:none; border:0; border-radius:12px; padding:12px 14px; font-size:1rem;
  background: var(--primary-color); color: var(--text-primary-color, #fff); cursor:pointer; flex:1; min-width:140px; }
button.sec { background: var(--secondary-background-color); color: var(--primary-text-color); border:1px solid var(--divider-color); }
button:disabled { opacity:.5; }
.face button, .lightbox .close { flex:none; min-width:0; }
textarea { width:100%; min-height:96px; box-sizing:border-box; border-radius:12px; padding:10px;
  border:1px solid var(--divider-color); background: var(--card-background-color); color: inherit; font-size:1rem; }
.ticket { border:1px solid var(--divider-color); border-radius:12px; padding:10px 12px; margin:8px 0; cursor:pointer; }
.ticket.on { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
.msg { padding:8px 0; border-bottom:1px solid var(--divider-color); white-space:pre-wrap; }
.msg b { display:block; font-size:.85rem; opacity:.8; }
.status { margin-top:8px; font-size:.9rem; }
.faces { display:grid; grid-template-columns: repeat(auto-fill, minmax(140px,1fr)); gap:10px; }
.face { border:1px solid var(--divider-color); border-radius:12px; overflow:hidden; display:flex; flex-direction:column; }
.face .thumb { display:block; width:100%; height:140px; padding:0; margin:0; border:0; border-radius:0;
  background:#111; cursor:zoom-in; overflow:hidden; }
.face .thumb:disabled { cursor:default; opacity:1; }
.face .thumb img, .face .ph { width:100%; height:140px; object-fit:cover; background:#111; display:block; pointer-events:none; }
.face .cap { padding:8px; display:flex; flex-direction:column; gap:8px; align-items:stretch; }
.face .uid { font-size:.75rem; opacity:.75; line-height:1.3; text-align:center;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.face .del { width:100%; }
dialog.lightbox { border:0; margin:0; padding:48px 16px 16px; width:100vw; height:100vh;
  max-width:none; max-height:none; background:rgba(0,0,0,.88); box-sizing:border-box; }
dialog.lightbox[open] { display:flex; align-items:center; justify-content:center; }
dialog.lightbox::backdrop { background:rgba(0,0,0,.72); }
dialog.lightbox img { max-width:min(96vw, 920px); max-height:calc(100vh - 96px); width:auto; height:auto;
  object-fit:contain; border-radius:12px; }
dialog.lightbox .close { position:absolute; top:12px; right:12px; width:auto; }
input[type=file] { display:none; }
.error { color: var(--error-color, #c00); }
`;

function faceUploadError(data) {
  if (!data) return "Ошибка загрузки";
  if (data.error) return String(data.error);
  const errors = data.errors;
  if (Array.isArray(errors) && errors.length) return errors.join("; ");
  if (errors) return String(errors);
  try { return JSON.stringify(data); } catch (e) { return "Ошибка загрузки"; }
}

function loadImageElement(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("decode"));
    img.src = src;
  });
}

async function jpegFromGalleryFile(file, index) {
  const maxEdge = 1280;
  const quality = 0.85;
  const outName = "face-" + (index + 1) + ".jpg";
  let width = 0;
  let height = 0;
  let draw = null;
  let release = () => {};
  try {
    if (typeof createImageBitmap === "function") {
      let bitmap;
      try {
        bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      } catch (e) {
        bitmap = await createImageBitmap(file);
      }
      width = bitmap.width;
      height = bitmap.height;
      draw = (ctx, w, h) => ctx.drawImage(bitmap, 0, 0, w, h);
      release = () => { if (bitmap && bitmap.close) bitmap.close(); };
    }
  } catch (e) {}
  if (!draw) {
    const url = URL.createObjectURL(file);
    try {
      const img = await loadImageElement(url);
      width = img.naturalWidth || img.width;
      height = img.naturalHeight || img.height;
      draw = (ctx, w, h) => ctx.drawImage(img, 0, 0, w, h);
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  if (!draw || !width || !height) {
    return new File([file], outName, { type: "image/jpeg" });
  }
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  const w = Math.max(1, Math.round(width * scale));
  const h = Math.max(1, Math.round(height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, w, h);
  draw(ctx, w, h);
  release();
  const blob = await new Promise((resolve) => {
    if (!canvas.toBlob) {
      resolve(null);
      return;
    }
    canvas.toBlob((item) => resolve(item), "image/jpeg", quality);
  });
  if (blob) return new File([blob], outName, { type: "image/jpeg" });
  const dataUrl = canvas.toDataURL("image/jpeg", quality);
  const bin = atob(dataUrl.split(",")[1]);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new File([arr], outName, { type: "image/jpeg" });
}

class DomonapCabinetCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this._selected = null;
    this._messages = [];
    this._busy = false;
    this._built = false;
    this.attachShadow({ mode: "open" });
  }
  setConfig(config) { this._config = config || {}; }
  getCardSize() { return 8; }
  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    else this._refreshLists();
  }
  _mode() { return this._config.mode === "face" ? "face" : "support"; }
  _st(id) { return (this._hass && this._hass.states[id]) || null; }
  _ticketsId() {
    return this._config.tickets || this._find("sensor.", "_support_tickets");
  }
  _facesId() {
    return this._config.faces || this._find("sensor.", "_face_pass");
  }
  _find(prefix, suffix) {
    if (!this._hass) return "";
    return Object.keys(this._hass.states).find((id) => id.startsWith(prefix) && id.endsWith(suffix)) || "";
  }
  _build() {
    this._built = true;
    const root = this.shadowRoot;
    root.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = CARD_CSS;
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    wrap.innerHTML = this._mode() === "face" ? this._faceHtml() : this._supportHtml();
    root.append(style, wrap);
    this._bind();
    this._refreshLists();
  }
  _supportHtml() {
    return `<h2>Поддержка</h2>
      <p class="hint">Все обращения из приложения DomoNAP. Нажмите обращение, чтобы открыть переписку.</p>
      <div class="list"></div>
      <div class="thread"></div>
      <textarea class="text" placeholder="Текст обращения или ответа"></textarea>
      <div class="row">
        <button class="reply">Ответить</button>
        <button class="create sec">Новое обращение</button>
      </div>
      <div class="status"></div>`;
  }
  _faceHtml() {
    return `<h2>Проход по лицу</h2>
      <p class="hint">Сфотографируйте себя или выберите снимки из галереи. С iPhone лучше JPEG, не HEIC: фото перекодируется автоматически.</p>
      <div class="faces"></div>
      <div class="row">
        <button class="cam">Сфотографировать</button>
        <button class="gal sec">Из галереи</button>
      </div>
      <input class="cam-in" type="file" accept="image/*" capture="user">
      <input class="gal-in" type="file" accept="image/*" multiple>
      <div class="status"></div>
      <dialog class="lightbox" aria-label="Просмотр фото">
        <button type="button" class="close">Закрыть</button>
        <img alt="Фото лица">
      </dialog>`;
  }
  _bind() {
    const $ = (s) => this.shadowRoot.querySelector(s);
    if (this._mode() === "face") {
      $(".cam").onclick = () => $(".cam-in").click();
      $(".gal").onclick = () => $(".gal-in").click();
      $(".cam-in").onchange = (e) => this._upload(e.target.files);
      $(".gal-in").onchange = (e) => this._upload(e.target.files);
      const box = $(".lightbox");
      const close = () => this._closeFacePreview();
      $(".close").onclick = (ev) => { ev.stopPropagation(); close(); };
      box.onclick = close;
      box.querySelector("img").onclick = (ev) => ev.stopPropagation();
      box.addEventListener("cancel", (ev) => { ev.preventDefault(); close(); });
      return;
    }
    $(".reply").onclick = () => this._send(false);
    $(".create").onclick = () => this._send(true);
  }
  _setStatus(text, isError) {
    const el = this.shadowRoot.querySelector(".status");
    if (!el) return;
    el.className = "status" + (isError ? " error" : "");
    el.textContent = text || "";
  }
  _refreshLists() {
    if (this._mode() === "face") this._renderFaces();
    else this._renderTickets();
  }
  _renderTickets() {
    const list = this.shadowRoot.querySelector(".list");
    if (!list || !this._hass) return;
    const st = this._st(this._ticketsId());
    const tickets = (st && st.attributes && st.attributes.tickets) || [];
    if (!tickets.length) {
      list.innerHTML = `<p class="hint">Обращений пока нет. Напишите текст и нажмите «Новое обращение».</p>`;
      return;
    }
    list.innerHTML = tickets.map((t) => {
      const id = t.ticketId || t.id || "";
      const on = this._selected === id ? " on" : "";
      return `<div class="ticket${on}" data-id="${id}">
        <b>${this._esc(t.theme || "Обращение")}</b>
        <div>${this._esc(t.status || "")} · ${this._esc(id.slice(-8))}</div>
        <div>${this._esc((t.text || "").slice(0, 180))}</div>
      </div>`;
    }).join("");
    list.querySelectorAll(".ticket").forEach((el) => {
      el.onclick = () => this._openTicket(el.dataset.id);
    });
  }
  _renderFaces() {
    const box = this.shadowRoot.querySelector(".faces");
    if (!box || !this._hass) return;
    const st = this._st(this._facesId());
    const faces = (st && st.attributes && st.attributes.faces) || [];
    if (!faces.length) {
      box.innerHTML = `<p class="hint">Фото ещё нет — добавьте снимок кнопками ниже.</p>`;
      return;
    }
    const imgs = Object.values(this._hass.states).filter((s) => s.entity_id.startsWith("image.") && s.entity_id.includes("_face_"));
    box.innerHTML = faces.map((f) => {
      const id = f.imageId || "";
      const img = imgs.find((s) => (s.attributes.imageId || "") === id);
      const src = (img && img.attributes && img.attributes.entity_picture) || "";
      const pic = src
        ? `<img src="${this._esc(src)}" alt="">`
        : `<div class="ph"></div>`;
      return `<div class="face" data-id="${this._esc(id)}">
        <button type="button" class="thumb" data-src="${this._esc(src)}" ${src ? "" : "disabled "}aria-label="Открыть фото">
          ${pic}
        </button>
        <div class="cap">
          <div class="uid" title="${this._esc(id)}">ID · ${this._esc(this._faceIdLabel(id))}</div>
          <button type="button" class="sec del" data-id="${this._esc(id)}">Удалить</button>
        </div>
      </div>`;
    }).join("");
    box.querySelectorAll(".thumb").forEach((btn) => {
      btn.onclick = () => this._openFacePreview(btn.dataset.src);
    });
    box.querySelectorAll(".del").forEach((btn) => {
      btn.onclick = (ev) => { ev.stopPropagation(); this._closeFacePreview(); this._deleteFace(btn.dataset.id); };
    });
  }
  _faceIdLabel(imageId) {
    const id = String(imageId || "");
    if (!id) return "—";
    return id.length > 10 ? id.slice(-8) : id;
  }
  _openFacePreview(src) {
    if (!src) return;
    const box = this.shadowRoot.querySelector(".lightbox");
    const img = box && box.querySelector("img");
    if (!box || !img) return;
    img.src = src;
    if (typeof box.showModal === "function") {
      if (!box.open) box.showModal();
    } else {
      box.setAttribute("open", "");
    }
  }
  _closeFacePreview() {
    const box = this.shadowRoot.querySelector(".lightbox");
    if (!box) return;
    if (typeof box.close === "function" && box.open) box.close();
    else box.removeAttribute("open");
    const img = box.querySelector("img");
    if (img) img.removeAttribute("src");
  }
  async _openTicket(id) {
    this._selected = id;
    this._renderTickets();
    const thread = this.shadowRoot.querySelector(".thread");
    thread.innerHTML = `<p class="hint">Загрузка переписки…</p>`;
    try {
      const resp = await this._hass.callService("domonap", "get_support_ticket_messages", { ticket_id: id }, {}, true, true);
      const payload = (resp && resp.response) || resp || {};
      const nested = payload.response || {};
      const items = payload.results || payload.items || payload.messages || payload.ticketMessages || nested.results || nested.items || nested.messages || [];
      const msgs = Array.isArray(items) ? items : [];
      if (!msgs.length) {
        thread.innerHTML = `<p class="hint">В этом обращении пока нет сообщений.</p>`;
        return;
      }
      thread.innerHTML = msgs.map((m) => {
        const text = m.text || m.message || m.content || "";
        const support = m.isSupport === true || m.isIncoming === true || /поддержк|оператор|support/i.test(String(m.nameCreatedBy || m.createdBy || m.name || ""));
        const name = support
          ? (m.nameCreatedBy || m.senderName || m.name || "Поддержка")
          : "Вы";
        return `<div class="msg ${support ? "them" : "me"}"><b>${this._esc(name)}</b>${this._esc(text)}</div>`;
      }).join("");
    } catch (err) {
      thread.innerHTML = `<p class="error">${this._esc(String(err))}</p>`;
    }
  }
  async _send(createNew) {
    const text = (this.shadowRoot.querySelector(".text").value || "").trim();
    if (!text) { this._setStatus("Введите текст", true); return; }
    this._setStatus(createNew ? "Создаю обращение…" : "Отправляю…");
    try {
      if (createNew) {
        await this._hass.callService("domonap", "create_support_ticket", { text });
        this._selected = null;
      } else {
        const data = { text };
        if (this._selected) data.ticket_id = this._selected;
        await this._hass.callService("domonap", "send_support_message", data);
      }
      this.shadowRoot.querySelector(".text").value = "";
      this._setStatus("Готово. Список обновится через несколько секунд.");
      if (this._selected && !createNew) this._openTicket(this._selected);
    } catch (err) {
      this._setStatus(String(err), true);
    }
  }
  async _upload(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    this._setStatus(`Загружаю ${files.length} фото…`);
    const fd = new FormData();
    try {
      for (let i = 0; i < files.length; i++) {
        let jpeg;
        try {
          jpeg = await jpegFromGalleryFile(files[i], i);
        } catch (e) {
          jpeg = new File([files[i]], `face-${i + 1}.jpg`, { type: "image/jpeg" });
        }
        fd.append("file", jpeg, jpeg.name || `face-${i + 1}.jpg`);
      }
      const resp = await fetch("/api/domonap/face", {
        method: "POST",
        headers: { Authorization: `Bearer ${this._hass.auth.data.access_token}` },
        body: fd,
      });
      const data = await resp.json();
      if (!resp.ok || data.ok === false) {
        throw new Error(faceUploadError(data));
      }
      this._setStatus(`Добавлено фото: ${data.created}`);
      setTimeout(() => this._refreshLists(), 2000);
    } catch (err) {
      this._setStatus(String(err), true);
    }
    this.shadowRoot.querySelector(".cam-in").value = "";
    this.shadowRoot.querySelector(".gal-in").value = "";
  }
  async _deleteFace(imageId) {
    if (!imageId) return;
    try {
      await this._hass.callService("domonap", "delete_face", { image_id: imageId });
      this._setStatus("Фото удалено");
    } catch (err) {
      this._setStatus(String(err), true);
    }
  }
  _esc(value) {
    return String(value || "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));
  }
}

if (!customElements.get("domonap-cabinet-card")) {
  customElements.define("domonap-cabinet-card", DomonapCabinetCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((item) => item && item.type === "domonap-cabinet-card")) {
  window.customCards.push({
    type: "domonap-cabinet-card",
    name: "DomoNAP кабинет",
    description: "Обращения поддержки и проход по лицу",
  });
}
