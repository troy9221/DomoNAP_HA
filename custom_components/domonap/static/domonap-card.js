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
textarea { width:100%; min-height:96px; box-sizing:border-box; border-radius:12px; padding:10px;
  border:1px solid var(--divider-color); background: var(--card-background-color); color: inherit; font-size:1rem; }
.ticket { border:1px solid var(--divider-color); border-radius:12px; padding:10px 12px; margin:8px 0; cursor:pointer; }
.ticket.on { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
.msg { padding:8px 0; border-bottom:1px solid var(--divider-color); white-space:pre-wrap; }
.msg b { display:block; font-size:.85rem; opacity:.8; }
.status { margin-top:8px; font-size:.9rem; }
.faces { display:grid; grid-template-columns: repeat(auto-fill, minmax(140px,1fr)); gap:10px; }
.face { border:1px solid var(--divider-color); border-radius:12px; overflow:hidden; }
.face img { width:100%; height:140px; object-fit:cover; background:#111; display:block; }
.face .cap { padding:8px; font-size:.85rem; display:flex; justify-content:space-between; gap:6px; align-items:center; }
input[type=file] { display:none; }
.error { color: var(--error-color, #c00); }
`;

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
      <p class="hint">Сфотографируйте себя или выберите несколько снимков из галереи. Так же, как аватар в приложении DomoNAP.</p>
      <div class="faces"></div>
      <div class="row">
        <button class="cam">Сфотографировать</button>
        <button class="gal sec">Из галереи</button>
      </div>
      <input class="cam-in" type="file" accept="image/*" capture="environment">
      <input class="gal-in" type="file" accept="image/*" multiple>
      <div class="status"></div>`;
  }
  _bind() {
    const $ = (s) => this.shadowRoot.querySelector(s);
    if (this._mode() === "face") {
      $(".cam").onclick = () => $(".cam-in").click();
      $(".gal").onclick = () => $(".gal-in").click();
      $(".cam-in").onchange = (e) => this._upload(e.target.files);
      $(".gal-in").onchange = (e) => this._upload(e.target.files);
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
      const pic = src ? `<img src="${src}">` : `<div style="height:140px"></div>`;
      return `<div class="face" data-id="${id}">${pic}<div class="cap"><span>${this._esc(f.faceName || "Фото")}</span>
        <button class="sec del" data-id="${id}">Удалить</button></div></div>`;
    }).join("");
    box.querySelectorAll(".del").forEach((btn) => {
      btn.onclick = (ev) => { ev.stopPropagation(); this._deleteFace(btn.dataset.id); };
    });
  }
  async _openTicket(id) {
    this._selected = id;
    this._renderTickets();
    const thread = this.shadowRoot.querySelector(".thread");
    thread.innerHTML = `<p class="hint">Загрузка переписки…</p>`;
    try {
      const resp = await this._hass.callService("domonap", "get_support_ticket_messages", { ticket_id: id }, {}, true, true);
      const payload = (resp && resp.response) || resp || {};
      const items = payload.results || payload.items || payload.messages || payload.ticketMessages || (payload.response && (payload.response.results || payload.response.items)) || [];
      const msgs = Array.isArray(items) ? items : [];
      if (!msgs.length) {
        thread.innerHTML = `<p class="hint">В этом обращении пока нет сообщений.</p>`;
        return;
      }
      thread.innerHTML = msgs.map((m) => {
        const text = m.text || m.message || m.content || "";
        const name = m.name || m.senderName || (m.isSupport ? "Поддержка" : "Вы");
        return `<div class="msg"><b>${this._esc(name)}</b>${this._esc(text)}</div>`;
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
    files.forEach((f, i) => fd.append("file", f, f.name || `face-${i + 1}.jpg`));
    try {
      const resp = await fetch("/api/domonap/face", {
        method: "POST",
        headers: { Authorization: `Bearer ${this._hass.auth.data.access_token}` },
        body: fd,
      });
      const data = await resp.json();
      if (!resp.ok || data.ok === false) throw new Error(data.error || data.errors || JSON.stringify(data));
      this._setStatus(`Добавлено фото: ${data.created}`);
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

customElements.define("domonap-cabinet-card", DomonapCabinetCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "domonap-cabinet-card",
  name: "DomoNAP кабинет",
  description: "Обращения поддержки и проход по лицу",
});
