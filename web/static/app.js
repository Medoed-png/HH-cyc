"use strict";

// ---------- утилиты ----------
const $ = (id) => document.getElementById(id);

// Выбранный сайт поиска работы (hh / superjob / all / …). Прокидывается в запросы.
let currentSite = localStorage.getItem("hh_site") || "hh";
const ALL_SITES = "all";
const SITE_NAMES = {};  // id сайта -> отображаемое имя (из /api/sites)

function api(path, body) {
  // Сайт добавляем в тело каждого POST, чтобы бэкенд знал, к какой сессии слать.
  const payload = Object.assign({ site: currentSite }, body || {});
  return authFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// Значения отмеченных чекбоксов внутри контейнера по id.
function checkedValues(containerId) {
  return Array.from($(containerId).querySelectorAll("input[type=checkbox]:checked"))
    .map(c => c.value);
}
// Отметить чекбоксы контейнера по массиву значений.
function setChecks(containerId, values) {
  const set = new Set(values || []);
  $(containerId).querySelectorAll("input[type=checkbox]").forEach(c => {
    c.checked = set.has(c.value);
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
    experience: $("experience").value,
    employment: checkedValues("employment"),
    schedule: checkedValues("schedule"),
    company_blacklist: $("company_blacklist").value,
    autopilot_enabled: $("autopilot_enabled").checked,
    autopilot_interval_minutes: $("autopilot_interval_minutes").value,
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
  const cfg = await (await authFetch("/api/config?site=" + currentSite)).json();
  for (const k of ["professions", "region", "salary_min", "exclude_words",
                   "include_words", "resume_name", "cover_letter",
                   "daily_limit", "max_pages", "experience",
                   "company_blacklist", "autopilot_interval_minutes"]) {
    if ($(k) && cfg[k] != null) $(k).value = cfg[k];
  }
  setChecks("employment", cfg.employment);
  setChecks("schedule", cfg.schedule);
  $("autopilot_enabled").checked = !!cfg.autopilot_enabled;
  $("autopilot-badge").style.display = cfg.autopilot_enabled ? "" : "none";
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
  (await authFetch("/api/suggest?site=" + currentSite + "&text=" + encodeURIComponent(t))).json();
const fetchCities = async (t) =>
  (await authFetch("/api/cities?site=" + currentSite + "&q=" + encodeURIComponent(t))).json();

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
  const key = (v.site || "") + ":" + v.id;  // id вакансий могут совпадать между сайтами
  let tr = rows.get(key);
  if (!tr) {
    tr = document.createElement("tr");
    tr.dataset.url = v.url;
    tr.dataset.index = order++;
    tr.innerHTML = "<td></td><td></td><td></td><td></td><td></td><td></td>";
    tr.addEventListener("click", () => {
      document.querySelectorAll(".vac-table tr.selected").forEach(r => r.classList.remove("selected"));
      tr.classList.add("selected");
    });
    tr.addEventListener("dblclick", () => window.open(v.url, "_blank"));
    $("vac-body").appendChild(tr);
    rows.set(key, tr);
  }
  const cells = tr.children;
  cells[0].textContent = SITE_NAMES[v.site] || v.site || "";
  cells[1].textContent = v.title;
  cells[2].textContent = v.company;
  cells[3].textContent = v.salary;
  cells[4].textContent = v.status;
  cells[5].textContent = v.note;
  tr.className = rowClass(v, parseInt(tr.dataset.index, 10));
  if (tr.dataset.selected) tr.classList.add("selected");
}

// ---------- ответы работодателей ----------
const chatCache = new Map();   // vacancy_id -> messages
const openPanels = new Map();  // vacancy_id -> panel element (открыта)
const respById = new Map();    // vacancy_id -> r

function statusBadge(status) {
  const s = status.toLowerCase();
  let cls = "b-gray";
  if (s.includes("собеседов") || s.includes("приглаш") || s.includes("оффер")) cls = "b-green";
  else if (s.includes("отказ")) cls = "b-red";
  else if (s === "просмотрен" || s.includes("сообщ")) cls = "b-blue";
  return `<span class="badge ${cls}">${status}</span>`;
}

function renderResponses(items, unread) {
  // Полоса с числом непрочитанных сообщений.
  const bar = $("resp-unread");
  if (unread > 0) {
    bar.style.display = "block";
    bar.innerHTML = `<i class="bi bi-chat-dots"></i> У вас ${unread} непрочитанных сообщений — нажмите «Посмотреть ответ», чтобы прочитать на hh.ru.`;
  } else {
    bar.style.display = "none";
  }

  const body = $("resp-body");
  body.innerHTML = "";
  chatCache.clear(); openPanels.clear(); respById.clear();
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="5" class="muted-cell">Ответов пока нет.</td></tr>';
    return;
  }
  for (const r of items) {
    respById.set(r.id, r);
    const tr = document.createElement("tr");
    const btnCls = r.responded ? "btn btn-sm btn-primary" : "btn btn-sm btn-outline-secondary";
    tr.innerHTML =
      `<td>${r.title}</td><td>${r.company || ""}</td><td>${r.date || ""}</td>` +
      `<td>${statusBadge(r.status)}</td>` +
      `<td><button class="${btnCls}">Посмотреть ответ</button></td>`;
    tr.addEventListener("dblclick", () => window.open(r.url, "_blank"));
    tr.querySelector("button").addEventListener("click", () => toggleMessages(tr, r));
    body.appendChild(tr);
  }
}

function buildMessages(panel, messages, url) {
  panel.innerHTML = "";
  if (!messages || !messages.length) {
    const p = document.createElement("div");
    p.className = "msg-empty";
    p.textContent = "Сообщений в чате нет.";
    panel.appendChild(p);
  } else {
    for (const m of messages) {
      const msg = document.createElement("div");
      msg.className = "msg";
      const head = document.createElement("div");
      head.className = "msg-head";
      const author = document.createElement("span");
      author.className = "msg-author";
      author.textContent = m.author || "Работодатель";
      const time = document.createElement("span");
      time.className = "msg-time";
      time.textContent = m.time || "";
      head.appendChild(author);
      head.appendChild(time);
      const text = document.createElement("div");
      text.className = "msg-text";
      text.textContent = m.text;
      msg.appendChild(head);
      msg.appendChild(text);
      panel.appendChild(msg);
    }
  }
  const link = document.createElement("a");
  link.className = "msg-link";
  link.href = url;
  link.target = "_blank";
  link.textContent = "Открыть полный чат на hh.ru →";
  panel.appendChild(link);
}

function toggleMessages(tr, r) {
  // Если панель уже открыта — закрыть.
  if (tr.nextSibling && tr.nextSibling.classList && tr.nextSibling.classList.contains("msg-row")) {
    tr.nextSibling.remove();
    openPanels.delete(r.id);
    return;
  }
  const row = document.createElement("tr");
  row.className = "msg-row";
  const td = document.createElement("td");
  td.colSpan = 5;
  const panel = document.createElement("div");
  panel.className = "chat-panel";
  td.appendChild(panel);
  row.appendChild(td);
  tr.after(row);
  openPanels.set(r.id, panel);

  if (chatCache.has(r.id)) {
    buildMessages(panel, chatCache.get(r.id), r.url);
  } else {
    panel.innerHTML = '<div class="msg-empty">Загружаю переписку…</div>';
    api("/api/chat", { vacancy_id: r.id });  // придёт через SSE (type: chat)
  }
}

function onChatLoaded(vacancyId, messages) {
  chatCache.set(vacancyId, messages);
  const panel = openPanels.get(vacancyId);
  const r = respById.get(vacancyId);
  if (panel && r) buildMessages(panel, messages, r.url);
}

// ---------- подключение аккаунта hh.ru ----------
const CONN_LABELS = {
  connected: ["● подключён", "b-green"],
  needs_sms: ["● нужен код из SMS", "b-blue"],
  needs_captcha: ["● нужна капча", "b-red"],
  invalid: ["не подключён", "b-gray"],
};

function renderConnStatus(st) {
  const badge = $("conn-badge");
  const [text, cls] = CONN_LABELS[st.status] || CONN_LABELS.invalid;
  badge.className = "badge " + cls;
  badge.textContent = text;
  if (st.username && !$("hh-username").value) $("hh-username").value = st.username;
  // Поле кода и кнопка «Отправить код» — только когда сайт запросил код.
  const needSms = st.status === "needs_sms";
  $("sms-label").style.display = needSms ? "" : "none";
  $("sms-field").style.display = needSms ? "" : "none";
  $("btn-send-sms").style.display = needSms ? "" : "none";
  if (needSms) $("hh-sms").focus();
}

async function loadConnStatus() {
  try {
    const st = await (await authFetch("/api/conn_status?site=" + currentSite)).json();
    renderConnStatus(st);
  } catch (e) { /* не критично */ }
}

// Список сайтов в выпадающий селектор; смена сайта переключает весь контекст.
async function loadSites() {
  let sites = [];
  try { sites = await (await authFetch("/api/sites")).json(); } catch (e) { return; }
  const sel = $("site-select");
  sel.innerHTML = "";
  for (const s of sites) {
    SITE_NAMES[s.id] = s.display_name;
    const o = document.createElement("option");
    o.value = s.id; o.textContent = s.display_name;
    sel.appendChild(o);
  }
  if (!sites.some(s => s.id === currentSite)) currentSite = (sites[0] || {}).id || "hh";
  sel.value = currentSite;
  sel.onchange = () => {
    currentSite = sel.value;
    localStorage.setItem("hh_site", currentSite);
    clearTable();
    setStatus(false);          // мгновенно поправить шапку/панель под новый режим
    loadConfig();
    loadConnStatus();
    loadStats();
    if (currentSite !== ALL_SITES) api("/api/check_login").catch(() => {});
  };
}

// ---------- прокси (per-user) ----------
async function loadProxy() {
  try {
    const st = await (await authFetch("/api/proxy")).json();
    const badge = $("proxy-badge");
    if (st.set) {
      badge.className = "badge b-green";
      badge.textContent = "задан";
      $("proxy-url").placeholder = st.proxy_url || "прокси задан";
    } else {
      badge.className = "badge b-gray";
      badge.textContent = "не задан";
    }
  } catch (e) { /* не критично */ }
}

// ---------- Telegram-уведомления ----------
async function loadTelegram() {
  try {
    const st = await (await authFetch("/api/telegram")).json();
    const badge = $("telegram-badge");
    if (st.set) { badge.className = "badge b-green"; badge.textContent = "включено"; }
    else { badge.className = "badge b-gray"; badge.textContent = "выключено"; }
    if (!st.bot_configured) {
      $("telegram-hint").textContent =
        "⚠️ Бот не настроен на сервере (нет TELEGRAM_BOT_TOKEN) — уведомления не отправятся.";
    }
  } catch (e) { /* не критично */ }
}

// ---------- статистика ----------
let _statsChart = null;

function renderStatsChart(s) {
  const el = $("stats-chart");
  if (!el || typeof Chart === "undefined") return;
  const invites = s.invitations ?? 0, reject = s.rejections ?? 0, viewed = s.viewed ?? 0;
  const other = Math.max(0, (s.applied_total ?? 0) - invites - reject - viewed);
  const data = {
    labels: ["Приглашения", "Отказы", "Просмотрено", "Без ответа"],
    datasets: [{
      data: [invites, reject, viewed, other],
      backgroundColor: ["#198754", "#dc3545", "#0dcaf0", "#6c757d"],
      borderWidth: 0,
    }],
  };
  if (_statsChart) {
    _statsChart.data = data;
    _statsChart.update();
  } else {
    _statsChart = new Chart(el, {
      type: "doughnut",
      data,
      options: {
        plugins: { legend: { position: "right", labels: { color: "#adb5bd", boxWidth: 12 } } },
        cutout: "62%",
      },
    });
  }
}

let _dailyChart = null;

function renderDailyChart(daily) {
  const el = $("daily-chart");
  if (!el || typeof Chart === "undefined" || !Array.isArray(daily)) return;
  const data = {
    labels: daily.map(d => d.date),
    datasets: [{
      label: "Откликов",
      data: daily.map(d => d.count),
      backgroundColor: "#0d6efd",
      borderRadius: 4,
    }],
  };
  const opts = {
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: "#adb5bd" }, grid: { display: false } },
      y: { beginAtZero: true, ticks: { color: "#adb5bd", precision: 0 },
           grid: { color: "rgba(255,255,255,.06)" } },
    },
  };
  if (_dailyChart) { _dailyChart.data = data; _dailyChart.update(); }
  else _dailyChart = new Chart(el, { type: "bar", data, options: opts });
}

async function loadStats() {
  try {
    const s = await (await authFetch("/api/stats?site=" + currentSite)).json();
    $("st-today").textContent = s.applied_today ?? 0;
    $("st-total").textContent = s.applied_total ?? 0;
    $("st-invites").textContent = s.invitations ?? 0;
    $("st-reject").textContent = s.rejections ?? 0;
    $("st-viewed").textContent = s.viewed ?? 0;
    $("st-conv").textContent = (s.conversion ?? 0) + "%";
    renderStatsChart(s);
    renderDailyChart(s.daily);
  } catch (e) { /* не критично */ }
}

let _statsTimer = null;
function scheduleStatsRefresh() {  // дебаунс: не дёргать на каждый отклик
  clearTimeout(_statsTimer);
  _statsTimer = setTimeout(loadStats, 1500);
}

// ---------- поток событий (SSE) ----------
function connectEvents() {
  // Токен в query: EventSource не умеет слать заголовок Authorization.
  const es = new EventSource("/api/events?token=" + encodeURIComponent(getToken()));
  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    // События приходят от всех сессий пользователя; в режиме «все сайты» показываем
    // все, иначе только выбранный сайт.
    if (currentSite !== ALL_SITES && msg.site && msg.site !== currentSite) return;
    if (msg.type === "log") logLine(msg.text);
    else if (msg.type === "login") setStatus(msg.logged_in);
    else if (msg.type === "vacancy") {
      upsertVacancy(msg.vacancy);
      if (msg.vacancy && msg.vacancy.status === "откликнулись") scheduleStatsRefresh();
    }
    else if (msg.type === "responses") { renderResponses(msg.items, msg.unread || 0); loadStats(); }
    else if (msg.type === "chat") onChatLoaded(msg.vacancy_id, msg.messages);
    else if (msg.type === "conn_status") renderConnStatus(msg);
  };
  es.onerror = () => logLine("Соединение с сервером прервано, переподключаюсь…");
}

function setStatus(loggedIn) {
  const el = $("status");
  // Режим «все сайты»: единый статус входа неприменим — нейтральный вид,
  // подключение/переподключение скрыты (вход настраивается на конкретном сайте).
  if (currentSite === ALL_SITES) {
    el.className = "status muted";
    el.textContent = "🌐 поиск по всем сайтам";
    if ($("connect-card")) $("connect-card").style.display = "none";
    if ($("btn-reconnect")) $("btn-reconnect").style.display = "none";
    return;
  }
  el.className = "status " + (loggedIn ? "ok" : "bad");
  el.textContent = loggedIn ? "● вы вошли" : "● не авторизованы";
  // Карточка подключения нужна только когда пользователь НЕ вошёл. Если он уже
  // залогинен (например, по сохранённым cookies) — прячем, чтобы не сбивать
  // повторным вводом логина/пароля от hh.ru.
  const card = $("connect-card");
  if (card) card.style.display = loggedIn ? "none" : "";
  // Кнопка «Подключить аккаунт» — наоборот, видна только когда уже вошли
  // (даёт выйти из сайта и подключить другой аккаунт по логину/паролю).
  const rb = $("btn-reconnect");
  if (rb) rb.style.display = loggedIn ? "" : "none";
}

// ---------- сворачивание разделов ----------
function bindCollapsibles() {
  document.querySelectorAll(".card-toggle").forEach((head) => {
    head.addEventListener("click", () => {
      head.closest(".card").classList.toggle("collapsed");
    });
  });
}

// ---------- модальное окно подтверждения ----------
let _modalOk = null;

function showModal(onConfirm) {
  _modalOk = onConfirm;
  $("modal").style.display = "flex";
}

function hideModal() {
  $("modal").style.display = "none";
  _modalOk = null;
}

function bindModal() {
  $("modal-cancel").onclick = hideModal;
  $("modal-ok").onclick = () => {
    const cb = _modalOk;
    hideModal();
    if (cb) cb();
  };
  // Клик по затемнению — отмена.
  $("modal").addEventListener("click", (e) => {
    if (e.target === $("modal")) hideModal();
  });
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
    $("modal-text").textContent =
      `Дневной лимит: ${d.daily_limit} откликов. Бот откликнется на найденные вакансии с сопроводительным письмом (строки перекрасятся по статусу).`;
    // Таблицу НЕ очищаем — откликаемся на уже найденные, строки обновятся на месте.
    showModal(() => api("/api/apply", d));
  };
  $("btn-stop").onclick = () => api("/api/stop");
  $("btn-responses").onclick = () => {
    $("resp-body").innerHTML = '<tr><td colspan="5" class="muted-cell">Загружаю…</td></tr>';
    api("/api/responses");
  };
  $("btn-open").onclick = openSelected;
  $("btn-show-browser").onclick = () => {
    logLine("Открываю окно браузера…");
    api("/api/show_browser");
  };
  $("btn-refresh-stats").onclick = loadStats;
  $("btn-reconnect").onclick = () => {
    logLine("Выхожу из аккаунта сайта — сейчас появится форма подключения…");
    api("/api/logout_site");  // сбросит cookies -> придёт login=false -> покажется панель
  };
  $("btn-connect").onclick = () => {
    const username = $("hh-username").value.trim();
    const password = $("hh-password").value;
    if (!username) { logLine("Укажите логин hh.ru (email или телефон)."); return; }
    // Пароль необязателен: без него — вход по коду из SMS/письма.
    logLine(password ? "Подключаю аккаунт hh.ru…"
                     : "Вхожу по коду — сейчас hh.ru пришлёт код…");
    api("/api/connect", { username, password });
  };
  $("btn-send-sms").onclick = () => {
    const code = $("hh-sms").value.trim();
    if (!code) { logLine("Введите код из SMS/письма."); return; }
    logLine("Отправляю код подтверждения…");
    api("/api/sms", { code });
  };
  $("btn-disconnect").onclick = async () => {
    await api("/api/disconnect");
    $("hh-password").value = "";
    $("hh-sms").value = "";
    logLine("Аккаунт hh.ru отключён.");
    loadConnStatus();
  };
  $("btn-save-proxy").onclick = async () => {
    await api("/api/proxy", { proxy_url: $("proxy-url").value.trim() });
    $("proxy-url").value = "";
    logLine("Прокси сохранён (применится при следующем запуске браузера).");
    loadProxy();
  };
  $("btn-clear-proxy").onclick = async () => {
    await api("/api/proxy", { proxy_url: "" });
    $("proxy-url").value = "";
    logLine("Прокси очищен.");
    loadProxy();
  };
  $("btn-save-telegram").onclick = async () => {
    const chat_id = $("telegram-chat-id").value.trim();
    if (!chat_id) { logLine("Укажите chat_id."); return; }
    const r = await (await api("/api/telegram", { chat_id })).json();
    logLine(r.test_sent ? "Telegram подключён — отправил тестовое сообщение."
                        : "Telegram сохранён, но тест не отправлен (проверьте chat_id и бот на сервере).");
    loadTelegram();
  };
  $("btn-clear-telegram").onclick = async () => {
    await api("/api/telegram", { chat_id: "" });
    $("telegram-chat-id").value = "";
    logLine("Telegram-уведомления отключены.");
    loadTelegram();
  };
  $("btn-save").onclick = async () => {
    await api("/api/save", collectForm());
    $("autopilot-badge").style.display = $("autopilot_enabled").checked ? "" : "none";
    logLine($("autopilot_enabled").checked
      ? "Критерии сохранены. Автопилот включён."
      : "Критерии сохранены.");
  };

  $("salary_min").addEventListener("input", (e) => {
    const pos = e.target.value.length;
    e.target.value = groupDigits(e.target.value);
  });
}

// ---------- старт ----------
window.addEventListener("DOMContentLoaded", async () => {
  if (!requireAuth()) return;  // нет токена -> на /login
  $("btn-logout").onclick = logout;
  try {
    const me = await (await authFetch("/auth/me")).json();
    $("user-email").textContent = me.email || "";
  } catch (e) { return; }  // 401 -> authFetch уже увёл на /login
  await loadSites();
  setStatus(false);  // применить режим (нейтральный для «все сайты»)
  await loadConfig();
  attachAutocomplete($("professions"), fetchProfessions, true);
  attachAutocomplete($("region"), fetchCities, false);
  bindButtons();
  bindModal();
  bindCollapsibles();
  connectEvents();
  loadConnStatus();                          // статус подключения аккаунта hh.ru
  loadProxy();                               // статус прокси пользователя
  loadTelegram();                            // статус Telegram-уведомлений
  loadStats();                               // дашборд статистики
  if (currentSite !== ALL_SITES) api("/api/check_login").catch(() => {});
  bindAutoLogin();
});

// Авто-переоткрытие окна hh.ru без кнопки «Войти»: когда пользователь
// возвращается во вкладку приложения, проверяем вход (и при необходимости
// заново открываем закрытое окно браузера). Дебаунс, чтобы не частить.
function bindAutoLogin() {
  let lastCheck = 0;
  const recheck = () => {
    if (document.visibilityState !== "visible") return;
    if (currentSite === ALL_SITES) return;  // в режиме «все» единый статус не нужен
    if (Date.now() - lastCheck < 5000) return;
    lastCheck = Date.now();
    api("/api/check_login").catch(() => {});
  };
  document.addEventListener("visibilitychange", recheck);
  window.addEventListener("focus", recheck);
}
