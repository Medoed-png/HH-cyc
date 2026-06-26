"use strict";

// ---------- утилиты ----------
const $ = (id) => document.getElementById(id);

function api(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function collectForm() {
  return {
    professions: $("professions").value,
    region: $("region").value,
    salary_min: $("salary_min").value,
    exclude_words: $("exclude_words").value,
    include_words: $("include_words").value,
    resume_name: $("resume_name").value,
    cover_letter: $("cover_letter").value,
    daily_limit: $("daily_limit").value,
    max_pages: $("max_pages").value,
  };
}

function logLine(text) {
  const log = $("log");
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

function groupDigits(value) {
  const digits = (value || "").replace(/\D/g, "");
  if (!digits) return "";
  return parseInt(digits, 10).toLocaleString("ru-RU").replace(/ /g, ".");
}

// ---------- загрузка/сохранение критериев ----------
async function loadConfig() {
  const cfg = await (await fetch("/api/config")).json();
  for (const k of ["professions", "region", "salary_min", "exclude_words",
                   "include_words", "resume_name", "cover_letter",
                   "daily_limit", "max_pages"]) {
    if ($(k) && cfg[k] != null) $(k).value = cfg[k];
  }
  $("salary_min").value = groupDigits($("salary_min").value);
}

// ---------- автоподсказки ----------
function attachAutocomplete(input, fetcher, multi) {
  const box = document.createElement("div");
  box.className = "suggest";
  box.style.display = "none";
  input.parentElement.appendChild(box);
  let items = [], active = -1, seq = 0, timer = null;

  const currentToken = () => {
    if (!multi) return input.value.trim();
    const v = input.value;
    return v.slice(v.lastIndexOf(",") + 1).trim();
  };

  const hide = () => { box.style.display = "none"; active = -1; };

  const render = () => {
    box.innerHTML = "";
    items.forEach((text, i) => {
      const d = document.createElement("div");
      d.textContent = text;
      if (i === active) d.classList.add("active");
      d.addEventListener("mousedown", (e) => { e.preventDefault(); choose(text); });
      box.appendChild(d);
    });
    box.style.display = items.length ? "block" : "none";
  };

  const choose = (text) => {
    if (!multi) {
      input.value = text;
    } else {
      const v = input.value;
      const idx = v.lastIndexOf(",");
      const prefix = idx >= 0 ? v.slice(0, idx + 1).trimEnd() : "";
      input.value = (prefix ? prefix + " " + text : text) + ", ";
    }
    hide();
    input.focus();
  };

  input.addEventListener("input", () => {
    const token = currentToken();
    if (token.length < 1) { hide(); return; }
    clearTimeout(timer);
    const mySeq = ++seq;
    timer = setTimeout(async () => {
      const result = await fetcher(token);
      if (mySeq !== seq || currentToken() !== token) return;
      items = result.slice(0, 10);
      active = -1;
      render();
    }, 200);
  });

  input.addEventListener("keydown", (e) => {
    if (box.style.display === "none") return;
    if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
    else if (e.key === "Enter" && active >= 0) { choose(items[active]); e.preventDefault(); }
    else if (e.key === "Escape") { hide(); }
  });

  input.addEventListener("blur", () => setTimeout(hide, 150));
}

const fetchProfessions = async (t) =>
  (await fetch("/api/suggest?text=" + encodeURIComponent(t))).json();
const fetchCities = async (t) =>
  (await fetch("/api/cities?q=" + encodeURIComponent(t))).json();

// ---------- таблица вакансий ----------
const rows = new Map();      // id -> <tr>
const profClass = new Map(); // профессия -> номер цвета
let order = 0;

function professionCount() {
  return $("professions").value.split(",").map(s => s.trim()).filter(Boolean).length;
}

function rowClass(v, index) {
  if (v.status === "откликнулись") return "st-applied";
  if (v.status === "пропущена") return "st-skipped";
  if (v.status === "ошибка") return "st-error";
  if (professionCount() >= 2 && v.profession) {
    if (!profClass.has(v.profession))
      profClass.set(v.profession, profClass.size % 8);
    return "prof" + profClass.get(v.profession);
  }
  return index % 2 ? "zebra-odd" : "zebra-even";
}

function clearTable() {
  $("vac-body").innerHTML = "";
  rows.clear();
  profClass.clear();
  order = 0;
}

function upsertVacancy(v) {
  let tr = rows.get(v.id);
  if (!tr) {
    tr = document.createElement("tr");
    tr.dataset.url = v.url;
    tr.dataset.index = order++;
    tr.innerHTML = "<td></td><td></td><td></td><td></td><td></td>";
    tr.addEventListener("click", () => {
      document.querySelectorAll(".vac-table tr.selected").forEach(r => r.classList.remove("selected"));
      tr.classList.add("selected");
    });
    tr.addEventListener("dblclick", () => window.open(v.url, "_blank"));
    $("vac-body").appendChild(tr);
    rows.set(v.id, tr);
  }
  const cells = tr.children;
  cells[0].textContent = v.title;
  cells[1].textContent = v.company;
  cells[2].textContent = v.salary;
  cells[3].textContent = v.status;
  cells[4].textContent = v.note;
  tr.className = rowClass(v, parseInt(tr.dataset.index, 10));
  if (tr.dataset.selected) tr.classList.add("selected");
}

// ---------- мои отклики (история) ----------
function formatDate(iso) {
  if (!iso) return "";
  return iso.replace("T", " ").slice(0, 16);  // YYYY-MM-DD HH:MM
}

async function loadApplied() {
  $("applied-body").innerHTML =
    '<tr><td colspan="3" class="muted-cell">Загружаю…</td></tr>';
  const items = await (await fetch("/api/applied")).json();
  const body = $("applied-body");
  body.innerHTML = "";
  if (!items.length) {
    body.innerHTML =
      '<tr><td colspan="3" class="muted-cell">Вы ещё ни на что не откликались.</td></tr>';
    return;
  }
  for (const r of items) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${r.title || "—"}</td><td>${r.company || ""}</td><td>${formatDate(r.applied_at)}</td>`;
    tr.style.cursor = "pointer";
    tr.title = "Открыть на hh.ru";
    tr.addEventListener("dblclick", () => window.open(r.url, "_blank"));
    body.appendChild(tr);
  }
}

// ---------- ответы работодателей ----------
function statusBadge(status) {
  const s = status.toLowerCase();
  let cls = "b-gray";
  if (s.includes("приглаш") || s.includes("оффер")) cls = "b-green";
  else if (s.includes("отказ")) cls = "b-red";
  else if (s.includes("просмотр") || s.includes("сообщ") || s === "ответ") cls = "b-blue";
  return `<span class="badge ${cls}">${status}</span>`;
}

function renderResponses(items) {
  const body = $("resp-body");
  body.innerHTML = "";
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="3" class="muted-cell">Ответов пока нет.</td></tr>';
    return;
  }
  for (const r of items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${r.title}</td><td>${r.company || ""}</td><td>${statusBadge(r.status)}</td>`;
    tr.style.cursor = "pointer";
    tr.title = "Открыть на hh.ru";
    tr.addEventListener("dblclick", () => window.open(r.url, "_blank"));
    body.appendChild(tr);
  }
}

// ---------- поток событий (SSE) ----------
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "log") logLine(msg.text);
    else if (msg.type === "login") setStatus(msg.logged_in);
    else if (msg.type === "vacancy") upsertVacancy(msg.vacancy);
    else if (msg.type === "responses") renderResponses(msg.items);
  };
  es.onerror = () => logLine("Соединение с сервером прервано, переподключаюсь…");
}

function setStatus(loggedIn) {
  const el = $("status");
  el.className = "status " + (loggedIn ? "ok" : "bad");
  el.textContent = loggedIn ? "● вы вошли" : "● не авторизованы";
}

// ---------- кнопки ----------
function openSelected() {
  const tr = document.querySelector(".vac-table tr.selected");
  if (tr && tr.dataset.url) window.open(tr.dataset.url, "_blank");
  else logLine("Выберите вакансию в таблице, чтобы открыть её.");
}

function bindButtons() {
  $("btn-search").onclick = () => { clearTable(); api("/api/search", collectForm()); };
  $("btn-apply").onclick = () => {
    const d = collectForm();
    if (!confirm(`Запустить АВТО-отклики?\nДневной лимит: ${d.daily_limit}.\nБот будет откликаться сам на все подходящие вакансии.`))
      return;
    clearTable();
    api("/api/apply", d);
  };
  $("btn-stop").onclick = () => api("/api/stop");
  $("btn-login").onclick = () => api("/api/login");
  $("btn-applied").onclick = loadApplied;
  $("btn-responses").onclick = () => {
    $("resp-body").innerHTML = '<tr><td colspan="3" class="muted-cell">Загружаю…</td></tr>';
    api("/api/responses");
  };
  $("btn-open").onclick = openSelected;
  $("btn-save").onclick = async () => { await api("/api/save", collectForm()); logLine("Критерии сохранены."); };

  $("salary_min").addEventListener("input", (e) => {
    const pos = e.target.value.length;
    e.target.value = groupDigits(e.target.value);
  });
}

// ---------- старт ----------
window.addEventListener("DOMContentLoaded", async () => {
  await loadConfig();
  attachAutocomplete($("professions"), fetchProfessions, true);
  attachAutocomplete($("region"), fetchCities, false);
  bindButtons();
  connectEvents();
});
