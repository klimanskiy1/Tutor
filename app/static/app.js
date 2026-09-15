/* Tutor — простой SPA на хэш-роутинге. */
const $app = document.getElementById("app");
const TYPE_NAMES = { choice: "Один вариант", multi: "Несколько вариантов", truefalse: "Верно / неверно", text: "Ввод текста", flash: "Карточка (самооценка)" };
const RATINGS = [[1, "Снова"], [2, "Трудно"], [3, "Хорошо"], [4, "Легко"]];

let keyHandler = null;
document.addEventListener("keydown", (e) => { if (keyHandler) keyHandler(e); });

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function toast(msg, ms = 2500) { const t = document.getElementById("toast"); t.textContent = msg; t.hidden = false; clearTimeout(t._t); t._t = setTimeout(() => (t.hidden = true), ms); }
async function api(method, url, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || r.statusText);
  return data;
}
function imgHtml(card) { return card.image ? `<img src="/media/${esc(card.image)}" alt="">` : ""; }

// ---------- роутер ----------
function route() {
  keyHandler = null;
  const h = location.hash.replace(/^#\/?/, "");
  const [view, arg, flag] = h.split("/");
  if (view === "study") return studyView(arg, flag === "cram");
  if (view === "edit") return editView(arg);
  if (view === "new") return editView(null);
  if (view === "stats") return statsView();
  return decksView();
}
window.addEventListener("hashchange", route);

// ---------- список колод ----------
async function decksView() {
  $app.innerHTML = "<p class='muted'>Загрузка…</p>";
  const decks = await api("GET", "/api/decks");
  let rows = decks.map((d) => {
    if (d.error) return `<tr><td colspan="5"><b>${esc(d.id)}</b> <span class="err">ошибка в файле: ${esc(d.error)}</span></td></tr>`;
    const nothing = !d.new && !d.due && !d.learning;
    return `<tr>
      <td><a href="#/study/${esc(d.id)}"><b>${esc(d.name)}</b></a><div class="muted">${esc(d.description)}</div></td>
      <td class="num"><span class="badge new" title="новые сегодня">${d.new}</span></td>
      <td class="num"><span class="badge learn" title="в обучении">${d.learning}</span></td>
      <td class="num"><span class="badge due" title="к повторению">${d.due}</span></td>
      <td class="num muted">${d.total}</td>
      <td class="num">
        <button class="primary" onclick="location.hash='#/study/${esc(d.id)}'" ${nothing ? "disabled" : ""}>Учить</button>
        <button onclick="location.hash='#/study/${esc(d.id)}/cram'" title="Без лимитов и расписания">Всё</button>
        <button onclick="location.hash='#/edit/${esc(d.id)}'">Правка</button>
      </td></tr>`;
  }).join("");
  if (!decks.length) rows = `<tr><td colspan="6" class="muted">Колод пока нет. Создай через кнопку или положи JSON в папку decks/.</td></tr>`;
  $app.innerHTML = `
    <div class="row" style="justify-content:space-between">
      <h1>Колоды</h1>
      <div>
        <button onclick="location.hash='#/new'">+ Новая колода</button>
        <button onclick="reloadDecks()" title="Перечитать decks/*.json">⟳ Перечитать файлы</button>
      </div>
    </div>
    <table><tr><th>Колода</th><th>Новые</th><th>Учу</th><th>Повтор</th><th>Всего</th><th></th></tr>${rows}</table>
    <p class="muted">Новые — сколько новых карточек ещё можно взять сегодня. Учу — в процессе заучивания (короткие интервалы). Повтор — пора повторить.</p>`;
}
async function reloadDecks() {
  const r = await api("POST", "/api/reload");
  const errs = Object.keys(r.errors || {});
  toast(errs.length ? "Ошибки в: " + errs.join(", ") : "Перечитано: " + r.decks.length);
  decksView();
}

// ---------- учёба ----------
async function studyView(deckId, cram) {
  let cur = null, phase = "ask", selected = new Set(), startedAt = 0, lastId = null, sessionDone = 0;

  async function load() {
    $app.innerHTML = "<p class='muted'>Загрузка…</p>";
    const q = new URLSearchParams(); if (cram) q.set("cram", "true"); if (cram && lastId) q.set("exclude", lastId);
    cur = await api("GET", `/api/decks/${deckId}/next?${q}`);
    phase = "ask"; selected = new Set(); startedAt = Date.now();
    render();
  }

  function header() {
    const c = cur.counts;
    return `<div class="row" style="justify-content:space-between">
      <div class="counts"><span class="badge new">${c.new}</span><span class="badge learn">${c.learning}</span><span class="badge due">${c.due}</span>
        <span>за сессию: ${sessionDone}</span>${cram ? "<span>· режим «всё»</span>" : ""}</div>
      <div><a href="#/edit/${esc(deckId)}">правка</a> · <a href="#/">колоды</a></div></div>`;
  }

  function render() {
    if (!cur.card) {
      const c = cur.counts;
      let msg = "На сегодня всё! ";
      if (c.new_total > c.new && c.new === 0) msg += `Лимит новых на сегодня исчерпан (осталось ${c.new_total} новых). `;
      if (c.next_due) msg += `Следующее повторение: ${new Date(c.next_due).toLocaleString()}.`;
      $app.innerHTML = header() + `<div class="card"><h2>${msg}</h2>
        <div class="row"><button onclick="location.hash='#/'">К колодам</button>
        <button onclick="location.hash='#/study/${esc(deckId)}/cram'">Учить всё без лимитов</button></div></div>`;
      keyHandler = null;
      // карточки в обучении подойдут через минуты — подождём и обновим
      const wait = c.next_due ? new Date(c.next_due) - Date.now() : Infinity;
      if (wait < 30 * 60 * 1000) setTimeout(() => { if (location.hash.includes(`/study/${deckId}`)) load(); }, Math.max(5000, wait + 500));
      return;
    }
    const card = cur.card;
    let body = "";
    if (card.type === "choice" || card.type === "multi") {
      body = `<div class="options">` + card.options.map((o, i) =>
        `<div class="opt ${selected.has(i) ? "selected" : ""}" data-i="${i}"><span class="key">${i + 1}</span><span>${esc(o)}</span></div>`).join("") + `</div>
        <p class="muted">${card.type === "multi" ? "Выбери все подходящие" : "Выбери один"} — цифры на клавиатуре, <kbd>Enter</kbd> — ответить</p>
        <div class="row"><button class="primary" id="submit" ${selected.size ? "" : "disabled"}>Ответить</button></div>`;
    } else if (card.type === "truefalse") {
      body = `<div class="row"><button class="primary" id="tf1">Верно <kbd>1</kbd></button><button class="primary" id="tf0">Неверно <kbd>2</kbd></button></div>`;
    } else if (card.type === "text") {
      body = `<div class="row"><input type="text" id="ans" placeholder="Ответ…" autocomplete="off"><button class="primary" id="submit">Ответить</button></div>
        <p class="muted"><kbd>Enter</kbd> — ответить</p>`;
    } else if (card.type === "flash") {
      body = `<div class="row"><button class="primary" id="submit">Показать ответ <kbd>Enter</kbd></button></div>`;
    }
    $app.innerHTML = header() + `<div class="card">
      <div class="muted">${cur.is_new ? "новая" : `повтор #${cur.reps}`} · ${TYPE_NAMES[card.type]}</div>
      <div class="question">${esc(card.question)}</div>${imgHtml(card)}${body}</div>`;

    $app.querySelectorAll(".opt").forEach((el) => el.onclick = () => toggle(+el.dataset.i));
    const s = document.getElementById("submit"); if (s) s.onclick = submit;
    const a = document.getElementById("ans"); if (a) a.focus();
    const tf1 = document.getElementById("tf1"); if (tf1) { tf1.onclick = () => submit(true); document.getElementById("tf0").onclick = () => submit(false); }

    keyHandler = (e) => {
      if (e.target.tagName === "INPUT" && e.key !== "Enter") return;
      if (card.type === "truefalse") { if (e.key === "1") submit(true); if (e.key === "2") submit(false); }
      else if ((card.type === "choice" || card.type === "multi") && /^[1-9]$/.test(e.key) && +e.key <= card.options.length) toggle(+e.key - 1);
      if (e.key === "Enter") { e.preventDefault(); submit(); }
    };
  }

  function toggle(i) {
    if (cur.card.type === "choice") selected = new Set([i]);
    else selected.has(i) ? selected.delete(i) : selected.add(i);
    render();
  }

  async function submit(tf) {
    const card = cur.card;
    let answer;
    if (card.type === "choice") { if (!selected.size) return; answer = [...selected][0]; }
    else if (card.type === "multi") { if (!selected.size) return; answer = [...selected].sort((a, b) => a - b); }
    else if (card.type === "truefalse") { if (typeof tf !== "boolean") return; answer = tf; }
    else if (card.type === "text") answer = document.getElementById("ans").value;
    else answer = null;
    keyHandler = null;
    const res = await api("POST", `/api/decks/${deckId}/check`, { card_id: card.id, answer });
    phase = "rate";
    renderResult(answer, res);
  }

  function renderResult(given, res) {
    const card = cur.card;
    let body = "", cls, title;
    if (card.type === "flash") { cls = "flash"; title = "Ответ"; }
    else if (res.correct && !res.near) { cls = "good"; title = "Верно"; }
    else if (res.near) { cls = "near"; title = "Почти (опечатка?)"; }
    else { cls = "bad"; title = "Неверно"; }

    if (card.type === "choice" || card.type === "multi") {
      const right = new Set(card.type === "choice" ? [res.answer] : res.answer);
      const mine = new Set(card.type === "choice" ? [given] : given);
      body = `<div class="options">` + card.options.map((o, i) => {
        const c = right.has(i) ? "right" : (mine.has(i) ? "wrong" : "");
        return `<div class="opt ${c}"><span class="key">${i + 1}</span><span>${esc(o)}${mine.has(i) ? " ←" : ""}</span></div>`;
      }).join("") + `</div>`;
    } else if (card.type === "truefalse") {
      body = `<div>Твой ответ: <b>${given ? "верно" : "неверно"}</b>. Правильно: <b>${res.answer ? "верно" : "неверно"}</b></div>`;
    } else if (card.type === "text") {
      body = `<div>Твой ответ: <b>${esc(given) || "—"}</b></div><div>Правильно: <span class="answer-text"><b>${esc(res.answer)}</b></span>${res.accept.length ? ` <span class="muted">(также: ${esc(res.accept.join(", "))})</span>` : ""}</div>`;
    } else {
      body = `<div class="answer-text">${esc(res.answer)}</div>`;
    }
    const sug = res.suggested;
    const hint = cls === "bad" ? `<p class="muted">Если на самом деле ответил верно — жми «Хорошо».</p>` : "";
    $app.innerHTML = header() + `<div class="card">
      <div class="question">${esc(card.question)}</div>${imgHtml(card)}
      <div class="result ${cls}"><b>${title}</b>${body}${res.explanation ? `<div class="explain">${esc(res.explanation)}</div>` : ""}</div>${hint}
      <div class="ratings">${RATINGS.map(([r, name]) => `<button data-r="${r}" class="${r === sug ? "suggested" : ""}">${name}<small>${esc(cur.intervals[r])} · <kbd>${r}</kbd></small></button>`).join("")}</div>
      <p class="muted"><kbd>Enter</kbd> — выбрать выделенное</p></div>`;
    $app.querySelectorAll(".ratings button").forEach((b) => b.onclick = () => rate(+b.dataset.r, res, given));
    keyHandler = (e) => {
      if (/^[1-4]$/.test(e.key)) rate(+e.key, res, given);
      if (e.key === "Enter") { e.preventDefault(); rate(sug, res, given); }
    };
  }

  async function rate(r, res, given) {
    keyHandler = null;
    const card = cur.card;
    // «я был прав» → correct = true, чтобы статистика не врала
    const correct = card.type === "flash" ? (r > 1) : (res.correct || r >= 3);
    await api("POST", `/api/decks/${deckId}/answer`, {
      card_id: card.id, rating: r, correct,
      answer: typeof given === "string" ? given : JSON.stringify(given), duration_ms: Date.now() - startedAt,
    });
    sessionDone++; lastId = card.id;
    load();
  }

  load().catch((e) => { $app.innerHTML = `<p class="err">${esc(e.message)}</p>`; });
}

// ---------- редактор ----------
async function editView(deckId) {
  let deck = deckId ? await api("GET", `/api/decks/${deckId}`) : { name: "", description: "", new_per_day: 20, cards: [] };
  deck.cards.forEach((c) => { c.accept = c.accept || []; });

  function blankCard(type) {
    const c = { type, question: "", answer: "", explanation: "" };
    if (type === "choice") { c.options = ["", ""]; c.answer = 0; }
    if (type === "multi") { c.options = ["", ""]; c.answer = []; }
    if (type === "truefalse") c.answer = true;
    if (type === "text") c.accept = [];
    return c;
  }

  function cardHtml(c, i) {
    let ans = "";
    if (c.type === "choice" || c.type === "multi") {
      ans = `<label>Варианты (отметь правильные)</label><div class="opts-edit">` + c.options.map((o, j) => {
        const checked = c.type === "choice" ? c.answer === j : c.answer.includes(j);
        return `<div class="row"><input type="${c.type === "choice" ? "radio" : "checkbox"}" name="ans${i}" ${checked ? "checked" : ""} data-act="mark" data-i="${i}" data-j="${j}">
          <input type="text" value="${esc(o)}" data-act="opt" data-i="${i}" data-j="${j}"><button data-act="delopt" data-i="${i}" data-j="${j}" title="Удалить вариант">×</button></div>`;
      }).join("") + `</div><button data-act="addopt" data-i="${i}">+ вариант</button>`;
    } else if (c.type === "truefalse") {
      ans = `<label>Правильный ответ</label><select data-act="tf" data-i="${i}"><option value="true" ${c.answer ? "selected" : ""}>Верно</option><option value="false" ${!c.answer ? "selected" : ""}>Неверно</option></select>`;
    } else if (c.type === "text") {
      ans = `<label>Правильный ответ</label><input type="text" value="${esc(c.answer)}" data-act="field" data-f="answer" data-i="${i}">
        <label>Также принимать (через ; )</label><input type="text" value="${esc(c.accept.join("; "))}" data-act="accept" data-i="${i}">`;
    } else {
      ans = `<label>Оборотная сторона</label><textarea data-act="field" data-f="answer" data-i="${i}">${esc(c.answer)}</textarea>`;
    }
    return `<div class="card-edit">
      <div class="head"><b>#${i + 1}</b>
        <select data-act="type" data-i="${i}">${Object.entries(TYPE_NAMES).map(([k, v]) => `<option value="${k}" ${c.type === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <button class="danger" data-act="del" data-i="${i}">Удалить</button></div>
      <label>Вопрос</label><textarea data-act="field" data-f="question" data-i="${i}">${esc(c.question)}</textarea>
      <label>Картинка (файл в decks/media/, необязательно)</label><input type="text" value="${esc(c.image || "")}" data-act="field" data-f="image" data-i="${i}">
      ${ans}
      <label>Пояснение (показывается после ответа, необязательно)</label><textarea data-act="field" data-f="explanation" data-i="${i}">${esc(c.explanation || "")}</textarea>
    </div>`;
  }

  function render() {
    $app.innerHTML = `<div class="row" style="justify-content:space-between"><h1>${deckId ? "Правка колоды" : "Новая колода"}</h1><a href="#/">колоды</a></div>
      <div class="card-edit">
        <label>Название</label><input type="text" id="dname" value="${esc(deck.name)}">
        <label>Описание</label><input type="text" id="ddesc" value="${esc(deck.description || "")}">
        <label>Новых карточек в день</label><input type="number" id="dnew" value="${deck.new_per_day || 20}" min="0" style="width:120px">
      </div>
      <div id="cards">${deck.cards.map(cardHtml).join("")}</div>
      <div class="row">
        <select id="newtype">${Object.entries(TYPE_NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
        <button id="addcard">+ Добавить карточку</button>
      </div>
      <div class="row" style="margin-top:20px">
        <button class="primary" id="save">Сохранить</button>
        ${deckId ? `<button id="reset" class="danger">Сбросить прогресс</button><button id="delete" class="danger">Удалить колоду</button>` : ""}
        <span class="muted">Колода хранится в decks/${esc(deckId || "<id>")}.json — можно править и руками.</span>
      </div>`;
    document.getElementById("addcard").onclick = () => { deck.cards.push(blankCard(document.getElementById("newtype").value)); render(); window.scrollTo(0, document.body.scrollHeight); };
    document.getElementById("save").onclick = save;
    const rs = document.getElementById("reset"); if (rs) rs.onclick = async () => { if (confirm("Сбросить весь прогресс по колоде?")) { await api("POST", `/api/decks/${deckId}/reset`); toast("Прогресс сброшен"); } };
    const dl = document.getElementById("delete"); if (dl) dl.onclick = async () => { if (confirm("Удалить колоду и файл?")) { await api("DELETE", `/api/decks/${deckId}`); location.hash = "#/"; } };
    $app.querySelector("#cards").addEventListener("input", onInput);
    $app.querySelector("#cards").addEventListener("change", onInput);
    $app.querySelector("#cards").addEventListener("click", onClick);
  }

  function onInput(e) {
    const el = e.target, i = +el.dataset.i, c = deck.cards[i]; if (!c) return;
    switch (el.dataset.act) {
      case "field": c[el.dataset.f] = el.value; break;
      case "opt": c.options[+el.dataset.j] = el.value; break;
      case "accept": c.accept = el.value.split(";").map((s) => s.trim()).filter(Boolean); break;
      case "tf": c.answer = el.value === "true"; break;
      case "mark": {
        const j = +el.dataset.j;
        if (c.type === "choice") c.answer = j;
        else c.answer = el.checked ? [...new Set([...c.answer, j])].sort() : c.answer.filter((x) => x !== j);
        break;
      }
      case "type": {
        const fresh = blankCard(el.value);
        deck.cards[i] = { ...fresh, id: c.id, question: c.question, image: c.image, explanation: c.explanation, options: c.options || fresh.options };
        if (el.value === "text" || el.value === "flash") deck.cards[i].answer = typeof c.answer === "string" ? c.answer : "";
        render(); break;
      }
    }
  }
  function onClick(e) {
    const el = e.target.closest("button"); if (!el) return;
    const i = +el.dataset.i, c = deck.cards[i];
    switch (el.dataset.act) {
      case "del": if (confirm("Удалить карточку?")) { deck.cards.splice(i, 1); render(); } break;
      case "addopt": c.options.push(""); render(); break;
      case "delopt": {
        const j = +el.dataset.j; c.options.splice(j, 1);
        if (c.type === "choice") c.answer = Math.min(c.answer, c.options.length - 1);
        else c.answer = c.answer.filter((x) => x !== j).map((x) => (x > j ? x - 1 : x));
        render(); break;
      }
    }
  }

  async function save() {
    deck.name = document.getElementById("dname").value.trim();
    deck.description = document.getElementById("ddesc").value.trim();
    deck.new_per_day = +document.getElementById("dnew").value || 20;
    const body = { ...deck, cards: deck.cards.map((c) => { const o = { ...c }; if (!o.image) delete o.image; if (!o.explanation) delete o.explanation; if (c.type !== "text") delete o.accept; else if (!o.accept.length) delete o.accept; return o; }) };
    try {
      if (deckId) { await api("PUT", `/api/decks/${deckId}`, body); toast("Сохранено"); }
      else { const r = await api("POST", "/api/decks", body); toast("Создано"); location.hash = `#/edit/${r.id}`; }
    } catch (e) { toast("Ошибка: " + e.message, 5000); }
  }
  render();
}

// ---------- статистика ----------
async function statsView() {
  const days = await api("GET", "/api/stats");
  $app.innerHTML = `<h1>Статистика</h1><table><tr><th>День</th><th>Ответов</th><th>Верно</th></tr>` +
    (days.length ? days.map((d) => `<tr><td>${d.day}</td><td class="num">${d.n}</td><td class="num">${d.ok} (${Math.round(100 * d.ok / d.n)}%)</td></tr>`).join("") : `<tr><td colspan="3" class="muted">Пока пусто</td></tr>`) + `</table>`;
}

route();
