"use strict";

// ---------- утилиты ----------
const $ = (id) => document.getElementById(id);

// Выбранный сайт поиска работы (hh / superjob / all / …). Прокидывается в запросы.
let currentSite = localStorage.getItem("hh_site") || "hh";
const ALL_SITES = "all";
const SITE_NAMES = {};  // id сайта -> отображаемое имя (из /api/sites)

// Авто-обновление ответов на отклики (минуты). Работает, пока вкладка открыта и
// пользователь авторизован. Кнопка «Обновить ответы» — ручной запуск в любой момент.
const RESPONSES_REFRESH_MIN = 5;
let _loggedIn = false;        // залогинен ли на выбранном сайте (из setStatus)
let _autoRespTimer = null;
let _lastRespReq = 0;         // когда последний раз ЗАПРАШИВАЛИ ответы (троттлинг)
let _lastRespAt = 0;          // когда последний раз ПРИШЛИ ответы (отметка времени)
let _pendingManualResp = false;  // обновление запрошено вручную (можно перерисовать)

function api(path, body) {
  // Сайт добавляем в тело каждого POST, чтобы бэкенд знал, к какой сессии слать.
  const payload = Object.assign({ site: currentSite }, body || {});
  return authFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// «Все страницы»: показать предупреждение и отключить поле max_pages (оно не нужно).
function applyAllPagesState() {
  const on = $("all_pages").checked;
  $("all-pages-warn").style.display = on ? "" : "none";
  $("max_pages").disabled = on;
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
    auto_letter: $("auto_letter").checked,
    daily_limit: $("daily_limit").value,
    max_pages: $("max_pages").value,
    all_pages: $("all_pages").checked,
    experience: $("experience").value,
    employment: checkedValues("employment"),
    schedule: checkedValues("schedule"),
    company_blacklist: $("company_blacklist").value,
    strict_title_match: $("strict_title_match").checked,
    autopilot_enabled: $("autopilot_enabled").checked,
    autopilot_interval_minutes: $("autopilot_interval_minutes").value,
    monitor_enabled: $("monitor_enabled").checked,
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
  // toLocaleString ru-RU разделяет тысячи неразрывным/узким пробелом
  // (U+00A0/U+202F), а не обычным — заменяем любой пробельный разделитель.
  return parseInt(digits, 10).toLocaleString("ru-RU").replace(/\s/gu, ".");
}

// ---------- загрузка/сохранение критериев ----------
async function loadConfig() {
  let cfg;
  try {
    cfg = await (await authFetch("/api/config?site=" + currentSite)).json();
  } catch (e) { return; }  // 401 уже увёл на /login; сетевую ошибку не роняем наружу
  for (const k of ["professions", "region", "salary_min", "exclude_words",
                   "include_words", "resume_name", "cover_letter",
                   "daily_limit", "max_pages", "experience",
                   "company_blacklist", "autopilot_interval_minutes"]) {
    if ($(k) && cfg[k] != null) $(k).value = cfg[k];
  }
  setChecks("employment", cfg.employment);
  setChecks("schedule", cfg.schedule);
  $("all_pages").checked = !!cfg.all_pages;
  applyAllPagesState();
  $("strict_title_match").checked = cfg.strict_title_match !== false;  // по умолчанию вкл
  $("auto_letter").checked = !!cfg.auto_letter;
  $("autopilot_enabled").checked = !!cfg.autopilot_enabled;
  $("monitor_enabled").checked = !!cfg.monitor_enabled;
  $("autopilot-badge").style.display = cfg.autopilot_enabled ? "" : "none";
  updateAutomationBadge();
  $("salary_min").value = groupDigits($("salary_min").value);
}

// Бейдж карточки «Автоматизация и письмо»: включён ли автопилот.
function updateAutomationBadge() {
  const b = $("automation-badge");
  if (!b) return;
  const ap = $("autopilot_enabled").checked, mon = $("monitor_enabled").checked;
  b.className = "badge " + (ap || mon ? "b-green" : "b-gray");
  b.textContent = ap && mon ? "автопилот + монитор"
                : ap ? "автопилот вкл" : mon ? "монитор вкл" : "выключено";
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
  tr._vac = v;  // полный объект вакансии на строке — для генерации письма
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
const chatCache = new Map();   // site:vacancy_id -> messages
const openPanels = new Map();  // site:vacancy_id -> panel element (открыта)
const respById = new Map();    // site:vacancy_id -> r
// Аккумулятор ответов по площадкам: в режиме «все сайты» каждое событие обновляет
// СВОЙ сайт, а таблица рисуется объединением — без затирания.
const _respStore = new Map();       // site -> {items, unread}
const _respLoggedOut = new Set();   // сайты с истёкшей сессией

// Собрать объединённую таблицу ответов из накопленных по сайтам событий.
function renderResponsesAccum() {
  let items = [], unread = 0;
  for (const [site, v] of _respStore.entries()) {
    (v.items || []).forEach(x => { x.site = x.site || site; });  // гарантировать site
    items = items.concat(v.items || []);
    unread += v.unread || 0;
  }
  const allLoggedOut = _respStore.size === 0 && _respLoggedOut.size > 0;
  renderResponses(items, unread, allLoggedOut);
}

function statusBadgeClass(status) {
  const s = (status || "").toLowerCase();
  if (s.includes("собеседов") || s.includes("приглаш") || s.includes("оффер")) return "b-green";
  if (s.includes("отказ")) return "b-red";
  if (s === "просмотрен" || s.includes("сообщ")) return "b-blue";
  return "b-gray";
}
// Бейдж статуса как DOM-элемент (textContent, без innerHTML) — статус приходит
// спарсенным со страниц сайтов, поэтому вставлять его как HTML небезопасно (XSS).
function statusBadgeEl(status) {
  const span = document.createElement("span");
  span.className = "badge " + statusBadgeClass(status);
  span.textContent = status || "";
  return span;
}

// «Просмотренные» отклики (где вы уже открывали ответ) — чтобы подсветка «новый
// ответ» гасла и не возвращалась после обновления. Храним ключи site:id.
const RESP_SEEN_KEY = "hh_resp_seen";
let respSeen = new Set();
try { respSeen = new Set(JSON.parse(localStorage.getItem(RESP_SEEN_KEY) || "[]")); } catch (e) {}
// Ключ ответа/чата — по РЕАЛЬНОМУ сайту элемента (r.site), а не по сайту поиска:
// иначе в режиме «все сайты» одинаковые id разных площадок конфликтовали бы, а
// «просмотрено»/чат адресовались бы не туда.
function chatKey(site, id) { return (site || currentSite) + ":" + id; }
function respKey(r) { return chatKey(r.site, r.id); }
function isNewResponse(r) { return !!r.responded && !respSeen.has(respKey(r)); }
let _lastUnread = 0;

// Плашка-счётчик сверху: сколько новых ответов и непрочитанных сообщений.
function renderUnreadBar(newCount, unread) {
  const bar = $("resp-unread");
  if (!bar) return;
  if (newCount <= 0 && unread <= 0) { bar.style.display = "none"; return; }
  bar.style.display = "block";
  const parts = [];
  if (newCount > 0) parts.push(`<b>${newCount}</b> ${plural(newCount, "новый ответ", "новых ответа", "новых ответов")}`);
  if (unread > 0) parts.push(`${unread} ${plural(unread, "непрочитанное сообщение", "непрочитанных сообщения", "непрочитанных сообщений")}`);
  bar.innerHTML = `<i class="bi bi-chat-dots"></i> ` + parts.join(" · ") +
    ` — подсвеченные строки содержат новый ответ; откройте «Посмотреть ответ», и подсветка пропадёт.`;
}

// Пересчитать число новых (после того как один ответ открыли) и обновить плашку.
function updateNewCountBar() {
  const newCount = [...respById.values()].filter(isNewResponse).length;
  renderUnreadBar(newCount, _lastUnread);
}
function markRespSeen(r) {
  const k = respKey(r);
  if (respSeen.has(k)) return;
  respSeen.add(k);
  try { localStorage.setItem(RESP_SEEN_KEY, JSON.stringify([...respSeen])); } catch (e) {}
}

function renderResponses(items, unread, loggedOut) {
  const body = $("resp-body");
  body.innerHTML = "";
  chatCache.clear(); openPanels.clear(); respById.clear();
  if (loggedOut) {
    $("resp-unread").style.display = "none";
    body.innerHTML = '<tr><td colspan="5" class="muted-cell">' +
      'Сессия сайта истекла — ответы не загрузить. Войдите заново в карточке ' +
      '«Подключение аккаунтов».</td></tr>';
    return;
  }
  if (!items.length) {
    $("resp-unread").style.display = "none";
    body.innerHTML = '<tr><td colspan="5" class="muted-cell">Ответов пока нет.</td></tr>';
    return;
  }
  _lastUnread = unread || 0;
  renderUnreadBar(items.filter(isNewResponse).length, _lastUnread);

  for (const r of items) {
    respById.set(respKey(r), r);
    const tr = document.createElement("tr");
    const isNew = isNewResponse(r);
    if (isNew) tr.classList.add("resp-new");
    // Безопасный рендер через textContent: title/company/status спарсены с сайтов
    // (неподконтрольный текст) — вставлять их как HTML нельзя (XSS).
    const tdTitle = document.createElement("td");
    if (isNew) {
      const dot = document.createElement("span");
      dot.className = "resp-new-dot"; dot.title = "Новый ответ";
      tdTitle.appendChild(dot);
    }
    tdTitle.appendChild(document.createTextNode(r.title || ""));
    const tdCompany = document.createElement("td"); tdCompany.textContent = r.company || "";
    const tdDate = document.createElement("td"); tdDate.textContent = r.date || "";
    const tdStatus = document.createElement("td"); tdStatus.appendChild(statusBadgeEl(r.status));
    const tdBtn = document.createElement("td");
    const btn = document.createElement("button");
    btn.className = isNew ? "btn btn-sm btn-success"
                         : (r.responded ? "btn btn-sm btn-primary" : "btn btn-sm btn-outline-secondary");
    btn.textContent = isNew ? "Открыть новый ответ" : "Посмотреть ответ";
    btn.addEventListener("click", () => toggleMessages(tr, r));
    tdBtn.appendChild(btn);
    tr.append(tdTitle, tdCompany, tdDate, tdStatus, tdBtn);
    tr.addEventListener("dblclick", () => window.open(r.url, "_blank"));
    body.appendChild(tr);
  }
}

// Склонение русских числительных: plural(n, "ответ","ответа","ответов").
function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
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
  const key = respKey(r);
  // Если панель уже открыта — закрыть.
  if (tr.nextSibling && tr.nextSibling.classList && tr.nextSibling.classList.contains("msg-row")) {
    tr.nextSibling.remove();
    openPanels.delete(key);
    return;
  }
  // Открыли ответ — гасим подсветку «новый» (запоминаем как просмотренный).
  markRespSeen(r);
  tr.classList.remove("resp-new");
  const dot = tr.querySelector(".resp-new-dot");
  if (dot) dot.remove();
  const btn = tr.querySelector("button");
  if (btn) { btn.className = "btn btn-sm btn-primary"; btn.textContent = "Посмотреть ответ"; }
  // Обновить плашку-счётчик новых сверху.
  updateNewCountBar();
  const row = document.createElement("tr");
  row.className = "msg-row";
  const td = document.createElement("td");
  td.colSpan = 5;
  const panel = document.createElement("div");
  panel.className = "chat-panel";
  td.appendChild(panel);
  row.appendChild(td);
  tr.after(row);
  openPanels.set(key, panel);

  if (chatCache.has(key)) {
    buildMessages(panel, chatCache.get(key), r.url);
  } else {
    panel.innerHTML = '<div class="msg-empty">Загружаю переписку…</div>';
    // Чат читаем на РЕАЛЬНОМ сайте отклика (в режиме «все» это может быть не currentSite).
    api("/api/chat", { vacancy_id: r.id, site: r.site || currentSite });  // придёт по SSE
  }
}

function onChatLoaded(site, vacancyId, messages) {
  const key = chatKey(site, vacancyId);
  chatCache.set(key, messages);
  const panel = openPanels.get(key);
  const r = respById.get(key);
  if (panel && r) buildMessages(panel, messages, r.url);
}

// ---------- подключение аккаунтов площадок ----------
const CONN_LABELS = {
  connected: ["● подключён", "b-green"],
  needs_sms: ["● нужен код из SMS", "b-blue"],
  // Капчей занимается всплывающее окно (showCaptchaModal) — постоянный бейдж
  // «нужна капча» не показываем, оставляем нейтральный статус.
  needs_captcha: ["не подключён", "b-gray"],
  invalid: ["не подключён", "b-gray"],
};

// Площадка, для которой сейчас открыта форма подключения (независимо от сайта поиска).
let connectSite = currentSite;
let connectMethods = [];      // способы входа выбранной площадки (из /api/login_methods)
let connectMode = null;       // id выбранного способа входа
let _lastConn = { status: "invalid" };  // последний статус подключения connectSite
let _awaitingCode = false;    // ждём ввод кода/капчи — не дёргать авто-check_login

function currentMethod() {
  return connectMethods.find(m => m.id === connectMode) || connectMethods[0]
    || { id: "manual", label: "Войти вручную в окне", fields: [], hint: "" };
}

function methodIcon(id) {
  if (id === "phone") return '<i class="bi bi-telephone"></i>';
  if (id === "email") return '<i class="bi bi-envelope"></i>';
  return '<i class="bi bi-window"></i>';
}

// Ряд бейджей площадок: цветная буква + название + точка статуса.
function renderSiteBadges(sites) {
  const box = $("connect-sites");
  box.innerHTML = "";
  sites.filter(s => s.id !== ALL_SITES).forEach(s => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "site-badge";
    el.dataset.site = s.id;
    el.innerHTML =
      `<span class="site-ic" style="background:${s.icon_color || "#6c757d"}">${s.icon_label || s.display_name.slice(0, 2)}</span>` +
      `<span class="site-nm">${s.display_name}</span>` +
      `<span class="site-dot" data-dot="${s.id}"></span>`;
    el.onclick = () => selectConnectSite(s.id);
    box.appendChild(el);
  });
}

// Состояние каждой площадки: статус кред (conn) + фактический вход (loggedIn).
// Точка зелёная, если вход выполнен ЛИБО креды подключены (даже вход по cookies).
const siteState = {};  // siteId -> { conn: "invalid", loggedIn: false }
function _st(siteId) { return (siteState[siteId] || (siteState[siteId] = { conn: "invalid", loggedIn: false })); }

function paintDot(siteId) {
  const dot = document.querySelector(`.site-dot[data-dot="${siteId}"]`);
  if (!dot) return;
  const s = _st(siteId);
  let cls = "", title = "не подключён";
  if (s.loggedIn || s.conn === "connected") { cls = "on"; title = "● вход выполнен"; }
  else if (s.conn === "needs_sms") { cls = "warn"; title = "● нужен код из SMS"; }
  dot.className = "site-dot " + cls;
  dot.title = title;
}

function setSiteConn(siteId, status) { _st(siteId).conn = status; paintDot(siteId); }
function setSiteLoggedIn(siteId, loggedIn) { _st(siteId).loggedIn = !!loggedIn; paintDot(siteId); }

// Кнопки-режимы способов входа выбранной площадки.
function renderConnectModes(methods) {
  const box = $("connect-modes");
  box.innerHTML = "";
  methods.forEach((m, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.mid = m.id;
    b.className = "btn " + (i === 0 ? "btn-primary active" : "btn-outline-primary");
    b.innerHTML = methodIcon(m.id) + " " + m.label;
    b.onclick = () => setConnectMode(m.id);
    box.appendChild(b);
  });
  box.style.display = methods.length > 1 ? "" : "none";  // один способ — выбор не нужен
}

function setConnectMode(id) {
  connectMode = id;
  $("connect-modes").querySelectorAll("button").forEach(b => {
    const on = b.dataset.mid === id;
    b.className = "btn " + (on ? "btn-primary active" : "btn-outline-primary");
  });
  renderConnectFields(currentMethod());
}

// Показать поля и кнопки под выбранный способ входа.
function renderConnectFields(m) {
  const f = m.fields || [];
  const hasUser = f.includes("username");
  const hasPass = f.includes("password");
  const hasSms = f.includes("sms_code");
  const credential = hasUser || hasPass;  // способ с вводом данных vs ручной/внешний

  $("password-label").style.display = hasPass ? "" : "none";
  $("password-field").style.display = hasPass ? "" : "none";

  const userField = $("hh-username").closest(".field");
  $("username-label").style.display = hasUser ? "" : "none";
  if (userField) userField.style.display = hasUser ? "" : "none";
  if (hasUser) {
    const phone = m.id === "phone";
    $("username-label").textContent = phone ? "Номер телефона" : (m.id === "email" ? "Email" : "Логин");
    $("hh-username").placeholder = phone ? "+7…" : (m.id === "email" ? "you@example.com" : "");
    $("hh-username").type = phone ? "tel" : (m.id === "email" ? "email" : "text");
  }

  // Кнопка входа: для способов с данными — «Войти»/«Получить код»; иначе — ручной вход.
  $("btn-connect").style.display = credential ? "" : "none";
  $("btn-manual-login").style.display = credential ? "none" : "";
  $("btn-connect").innerHTML = (hasSms && !hasPass)
    ? '<i class="bi bi-box-arrow-in-right"></i> Получить код по SMS'
    : '<i class="bi bi-box-arrow-in-right"></i> Войти';
  $("connect-hint").textContent = m.hint || "";
}

// Подключён ли реально выбранный сайт (по статусу кред или по фактическому входу).
function effectiveConnected(st) {
  return (st && st.status === "connected") || _st(connectSite).loggedIn;
}

function togglePanelConnected(connected) {
  $("connect-login").style.display = connected ? "none" : "";
  $("connect-connected").style.display = connected ? "" : "none";
  $("btn-relogin").style.display = connected ? "" : "none";
}

// Бейдж статуса аккаунта в шапке карточки. Если вход выполнен любым способом
// (сервером по паролю ИЛИ вручную/по кукам) — показываем «● подключён», а не
// «не подключён» из-за отсутствия сохранённого пароля.
function refreshConnectBadge() {
  const badge = $("conn-badge");
  if (!badge) return;
  let text, cls;
  if (effectiveConnected(_lastConn)) { text = "● подключён"; cls = "b-green"; }
  else { [text, cls] = CONN_LABELS[_lastConn.status] || CONN_LABELS.invalid; }
  badge.className = "badge " + cls;
  badge.textContent = text;
}

function renderConnStatus(st) {
  _lastConn = st || { status: "invalid" };
  setSiteConn(connectSite, _lastConn.status);
  refreshConnectBadge();
  if (_lastConn.username && !$("hh-username").value) $("hh-username").value = _lastConn.username;
  // Идёт интерактивный вход (ждём код/капчу) — авто-проверка входа не должна
  // дёргать сервер (он бы навигировал со страницы кода и сломал ввод кода).
  _awaitingCode = (_lastConn.status === "needs_sms" || _lastConn.status === "needs_captcha");
  // Поле кода и кнопка «Отправить код» — только когда сайт запросил код.
  const needSms = _lastConn.status === "needs_sms";
  $("sms-label").style.display = needSms ? "" : "none";
  $("sms-field").style.display = needSms ? "" : "none";
  $("btn-send-sms").style.display = needSms ? "" : "none";
  togglePanelConnected(effectiveConnected(_lastConn));
  if (needSms) $("hh-sms").focus();
}

// Сбросить панель подключения в нейтральное состояние (синхронно). Без этого при
// смене площадки оставались видимыми поле кода и старый _lastConn, и «Отправить
// код» мог уйти на НОВЫЙ сайт код, полученный для прежнего.
function resetConnectPanel() {
  _lastConn = { status: "invalid" };
  _awaitingCode = false;
  $("hh-username").value = "";
  $("hh-password").value = "";
  $("hh-sms").value = "";
  $("sms-label").style.display = "none";
  $("sms-field").style.display = "none";
  $("btn-send-sms").style.display = "none";
  refreshConnectBadge();
  togglePanelConnected(false);
}

// Загрузить статус подключения сайта (для точки и, если это выбранный сайт, для панели).
async function loadConnStatusFor(siteId) {
  try {
    const st = await (await authFetch("/api/conn_status?site=" + siteId)).json();
    setSiteConn(siteId, st.status);
    if (siteId === connectSite) renderConnStatus(st);
  } catch (e) {
    if (siteId === connectSite) renderConnStatus({ status: "invalid" });  // нейтрально, не зависаем
  }
}

// Выбрать площадку для подключения: загрузить её способы входа и статус.
async function selectConnectSite(siteId) {
  connectSite = siteId;
  resetConnectPanel();  // синхронно: убрать поле кода/старый статус прежней площадки
  $("connect-sites").querySelectorAll(".site-badge").forEach(el =>
    el.classList.toggle("selected", el.dataset.site === siteId));
  $("connect-site-title").textContent = "Аккаунт: " + (SITE_NAMES[siteId] || siteId);
  try {
    connectMethods = await (await authFetch("/api/login_methods?site=" + siteId)).json();
  } catch (e) { connectMethods = []; }
  if (!connectMethods || !connectMethods.length) {
    connectMethods = [{ id: "manual", label: "Войти вручную в окне", fields: [], hint: "" }];
  }
  renderConnectModes(connectMethods);
  setConnectMode(connectMethods[0].id);
  loadConnStatusFor(siteId);
}

// ---------- капча: всплывающее окно поверх всего + системное уведомление ----------
let captchaSite = null;

function showCaptchaModal(site) {
  captchaSite = site || connectSite || currentSite;
  const name = SITE_NAMES[captchaSite] || captchaSite || "сайт";
  const t = $("captcha-modal-text");
  if (t) t.innerHTML =
    `Сайт <b>${name}</b> показал капчу — её нужно пройти вручную. ` +
    `Нажмите «Пройти капчу»: откроется окно браузера на странице входа. ` +
    `Пройдите капчу, введите телефон и код — вход сохранится.`;
  $("captcha-modal").style.display = "flex";
  try { window.focus(); } catch (e) { /* no-op */ }
  notifyCaptcha(name);  // системное уведомление поверх других окон (если разрешено)
}

function hideCaptchaModal() { $("captcha-modal").style.display = "none"; }

function notifyCaptcha(name) {
  try {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const n = new Notification("HH-бот: требуется пройти капчу", {
      body: `Сайт ${name}: нажмите «Пройти капчу», чтобы открыть браузер и войти.`,
      requireInteraction: true,
    });
    n.onclick = () => { try { window.focus(); } catch (e) {} n.close(); };
  } catch (e) { /* no-op */ }
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
  // Ряд бейджей площадок для подключения аккаунтов + точки статуса.
  renderSiteBadges(sites);
  sites.filter(s => s.id !== ALL_SITES).forEach(s => loadConnStatusFor(s.id));
  const startSite = (currentSite !== ALL_SITES) ? currentSite
    : (sites.find(s => s.id !== ALL_SITES) || {}).id || "hh";
  selectConnectSite(startSite);
  sel.onchange = () => {
    currentSite = sel.value;
    localStorage.setItem("hh_site", currentSite);
    clearTable();
    _respStore.clear(); _respLoggedOut.clear();  // ответы прошлого сайта не тащим
    _lastRespReq = 0;          // сменили сайт — загрузить ответы заново при входе
    setStatus(false);          // мгновенно поправить шапку под новый режим
    loadConfig();
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

// ---------- тема (тёмная/светлая) ----------
const THEME_KEY = "hh_theme";
function currentTheme() { return localStorage.getItem(THEME_KEY) || "dark"; }
function applyTheme(theme) {
  document.documentElement.setAttribute("data-bs-theme", theme);
  const icon = $("btn-theme") && $("btn-theme").querySelector("i");
  if (icon) icon.className = theme === "light" ? "bi bi-sun" : "bi bi-moon-stars";
  // Пересобрать график под цвета темы (граница сегментов/цвет легенды).
  if (_lastStats) {
    if (_statsChart) { _statsChart.destroy(); _statsChart = null; }
    renderStatsChart(_lastStats);
  }
}
function toggleTheme() {
  const next = currentTheme() === "light" ? "dark" : "light";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
}

// ---------- статистика ----------
let _statsChart = null;
let _lastStats = null;

function renderStatsChart(s) {
  const el = $("stats-chart");
  if (!el || typeof Chart === "undefined") return;
  // Цвета границы/легенды берём из переменных активной темы (адаптивно).
  const cs = getComputedStyle(document.body);
  const panel = cs.getPropertyValue("--panel").trim() || "#161b25";
  const muted = cs.getPropertyValue("--muted").trim() || "#8a94a8";
  const invites = s.invitations ?? 0, reject = s.rejections ?? 0, viewed = s.viewed ?? 0;
  const other = Math.max(0, (s.applied_total ?? 0) - invites - reject - viewed);
  const data = {
    labels: ["Приглашения", "Отказы", "Просмотрено", "Без ответа"],
    datasets: [{
      data: [invites, reject, viewed, other],
      backgroundColor: ["#37d39b", "#f26a7e", "#5fb0ff", "#8a95a8"],
      borderColor: panel,
      borderWidth: 2,
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
        plugins: { legend: { position: "right", labels: { color: muted, boxWidth: 12, font: { family: "JetBrains Mono", size: 11 } } } },
        cutout: "62%",
      },
    });
  }
}

async function loadStats() {
  try {
    const s = await (await authFetch("/api/stats?site=" + currentSite)).json();
    _lastStats = s;
    $("st-today").textContent = s.applied_today ?? 0;
    $("st-total").textContent = s.applied_total ?? 0;
    $("st-invites").textContent = s.invitations ?? 0;
    $("st-reject").textContent = s.rejections ?? 0;
    $("st-viewed").textContent = s.viewed ?? 0;
    $("st-conv").textContent = (s.conversion ?? 0) + "%";
    renderStatsChart(s);
  } catch (e) { /* не критично */ }
}

// Запросить ответы, если вошли, выбран конкретный сайт, вкладка активна и прошло
// не меньше N минут с прошлого запроса (троттлинг). Используется и для авто-, и
// для первичной загрузки сразу после входа.
function maybeAutoRefresh() {
  if (currentSite === ALL_SITES || !_loggedIn) return;
  if (document.visibilityState !== "visible") return;
  if (Date.now() - _lastRespReq < RESPONSES_REFRESH_MIN * 60000) return;
  _lastRespReq = Date.now();
  api("/api/responses");  // ответ придёт по SSE -> renderResponses + отметка времени
}

// Плашка: время последнего обновления + период авто-обновления.
function updateRespBadge() {
  const badge = $("responses-auto");
  if (!badge) return;
  if (_lastRespAt) {
    const d = new Date(_lastRespAt);
    const t = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    badge.textContent = `обновлено в ${t} · авто каждые ${RESPONSES_REFRESH_MIN} мин`;
  } else {
    badge.textContent = `авто ⟳ каждые ${RESPONSES_REFRESH_MIN} мин`;
  }
}

// Авто-обновление ответов раз в RESPONSES_REFRESH_MIN минут. Проверяем часто
// (30с), но запрашиваем не чаще раза в N минут — так обновление срабатывает и
// после возврата из фоновой вкладки, а не «молчит» до следующего длинного тика.
function startResponsesAutoRefresh() {
  updateRespBadge();
  if (_autoRespTimer) return;  // не плодить таймеры
  _autoRespTimer = setInterval(maybeAutoRefresh, 30000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") maybeAutoRefresh();
  });
}

let _statsTimer = null;
function scheduleStatsRefresh() {  // дебаунс: не дёргать на каждый отклик
  clearTimeout(_statsTimer);
  _statsTimer = setTimeout(loadStats, 1500);
}

// ---------- поток событий (SSE) ----------
let _es = null;
let _esReconnectDelay = 1000;

// EventSource не умеет слать заголовок, а долгоживущий JWT в URL утекает в логи —
// поэтому берём одноразовый краткоживущий билет (POST с токеном в заголовке) и
// подключаемся по нему; при обрыве — новый билет с экспоненциальным backoff.
async function connectEvents() {
  let ticket;
  try {
    const r = await authFetch("/api/events/ticket", { method: "POST" });
    ticket = (await r.json()).ticket;
  } catch (e) { scheduleEventsReconnect(); return; }  // 401 уже увёл на /login
  if (!ticket) { scheduleEventsReconnect(); return; }
  _es = new EventSource("/api/events?ticket=" + encodeURIComponent(ticket));
  _es.onopen = () => { _esReconnectDelay = 1000; };  // подключились — сбросить backoff
  _es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    // Статус подключения относится к панели подключения (её сайт connectSite),
    // а не к сайту поиска — обрабатываем отдельно, до фильтра по currentSite.
    if (msg.type === "conn_status") {
      if (msg.site) setSiteConn(msg.site, msg.status);
      if (!msg.site || msg.site === connectSite) renderConnStatus(msg);
      if (msg.status === "needs_captcha") showCaptchaModal(msg.site);
      return;
    }
    // Вход выполнен/снят — красим точку любой площадки (зелёная = вход выполнен),
    // плюс обновляем шапку для текущего сайта поиска. Тоже до фильтра по currentSite.
    if (msg.type === "login") {
      if (msg.site) {
        setSiteLoggedIn(msg.site, msg.logged_in);
        // Явный выход: сбрасываем статус кред в UI, иначе устаревший 'connected'
        // продолжал бы держать точку зелёной и панель в состоянии «подключён».
        if (!msg.logged_in) setSiteConn(msg.site, "invalid");
      }
      if (!msg.site || msg.site === currentSite) setStatus(msg.logged_in);
      if (msg.site === connectSite) {
        if (!msg.logged_in) _lastConn = { status: "invalid" };
        togglePanelConnected(effectiveConnected(_lastConn)); refreshConnectBadge();
      }
      return;
    }
    // Завершение операции — снять блокировку кнопки (до фильтра по сайту: connect
    // мог идти на connectSite ≠ сайт поиска).
    if (msg.type === "done") { clearBusy(msg.op); return; }
    // Прочие события приходят от всех сессий пользователя; в режиме «все сайты»
    // показываем все, иначе только выбранный сайт поиска.
    if (currentSite !== ALL_SITES && msg.site && msg.site !== currentSite) return;
    if (msg.type === "log") logLine(msg.text);
    else if (msg.type === "vacancy") {
      upsertVacancy(msg.vacancy);
      if (msg.vacancy && msg.vacancy.status === "откликнулись") scheduleStatsRefresh();
    }
    else if (msg.type === "responses") {
      const site = msg.site || currentSite;
      // Аккумулируем ответы ПО САЙТУ (в режиме «все» события разных площадок не
      // затирают друг друга — раньше это была первопричина «видно только последний»).
      if (msg.logged_out) { _respLoggedOut.add(site); _respStore.delete(site); }
      else {
        _respLoggedOut.delete(site);
        _respStore.set(site, { items: msg.items || [], unread: msg.unread || 0 });
      }
      const manual = _pendingManualResp; _pendingManualResp = false;
      // Авто-обновление при ОТКРЫТОМ чате не перерисовываем (переписка бы схлопнулась).
      if (manual || openPanels.size === 0) renderResponsesAccum();
      if (msg.logged_out) {
        if (currentSite !== ALL_SITES && site === currentSite) setStatus(false);
      } else {
        _lastRespAt = Date.now(); updateRespBadge();
      }
      loadStats();
    }
    else if (msg.type === "chat") onChatLoaded(msg.site, msg.vacancy_id, msg.messages);
    else if (msg.type === "letter") {
      $("letter-text").value = msg.text || "";
      $("letter-sub").textContent = (msg.title || "") + (msg.company ? " · " + msg.company : "");
      const link = $("letter-open"); if (link) link.href = msg.url || "#";
      $("letter-modal").style.display = "flex";
    }
  };
  _es.onerror = () => {
    if (_es) { _es.close(); _es = null; }  // не даём EventSource долбить мёртвым билетом
    logLine("Соединение с сервером прервано, переподключаюсь…");
    scheduleEventsReconnect();
  };
}

function scheduleEventsReconnect() {
  setTimeout(connectEvents, _esReconnectDelay);
  _esReconnectDelay = Math.min(_esReconnectDelay * 2, 15000);  // backoff до 15с
}

function setStatus(loggedIn) {
  const el = $("status");
  // Режим «все сайты»: единый статус входа неприменим — нейтральный вид,
  // «Сменить аккаунт» в шапке скрыт (вход настраивается на конкретном сайте).
  // Карточка подключения остаётся видимой — в ней можно подключить любой сайт.
  if (currentSite === ALL_SITES) {
    _loggedIn = false;
    el.className = "status muted";
    el.textContent = "🌐 поиск по всем сайтам";
    if ($("btn-reconnect")) $("btn-reconnect").style.display = "none";
    return;
  }
  _loggedIn = loggedIn;  // для авто-обновления ответов (только когда вошли)
  el.className = "status " + (loggedIn ? "ok" : "bad");
  el.textContent = loggedIn ? "● вы вошли" : "● не авторизованы";
  // Кнопка «Сменить аккаунт» в шапке — видна, когда вошли на сайте поиска.
  const rb = $("btn-reconnect");
  if (rb) rb.style.display = loggedIn ? "" : "none";
  // Если форма подключения открыта на том же сайте — отразить вход (вход мог
  // случиться по сохранённым cookies, без серверного логина).
  if (connectSite === currentSite) { togglePanelConnected(effectiveConnected(_lastConn)); refreshConnectBadge(); }
  // Первичная авто-загрузка ответов сразу после входа (далее — раз в N минут).
  if (loggedIn) maybeAutoRefresh();
}

// ---------- сворачивание разделов ----------
function bindCollapsibles() {
  document.querySelectorAll(".card-toggle").forEach((head) => {
    head.addEventListener("click", () => {
      head.closest(".card").classList.toggle("collapsed");
    });
  });
}

// ---------- блокировка кнопок на время операции (анти-двойной-клик) ----------
// Двойной клик ставил в очередь несколько команд (повторные навигации формы,
// лишние капчи, дубли запросов к сайту → риск анти-бана). Блокируем кнопку до
// прихода события "done" по соответствующей операции (с фолбэком по таймауту).
const OP_BUTTON = { connect: "btn-connect", submit_sms: "btn-send-sms",
                    search: "btn-search", apply: "btn-apply", responses: "btn-responses",
                    letter: "btn-letter" };
const _busyOps = {};  // op -> { btn, html, timer }

function setBusy(op) {
  const btn = $(OP_BUTTON[op]);
  if (!btn || _busyOps[op]) return;
  _busyOps[op] = {
    btn, html: btn.innerHTML,
    timer: setTimeout(() => clearBusy(op), 120000),  // фолбэк, если "done" не придёт
  };
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
}

function clearBusy(op) {
  const b = _busyOps[op];
  if (!b) return;
  clearTimeout(b.timer);
  b.btn.disabled = false;
  b.btn.innerHTML = b.html;
  delete _busyOps[op];
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
  $("btn-search").onclick = () => { clearTable(); api("/api/search", collectForm()); setBusy("search"); };
  $("btn-apply").onclick = () => {
    const d = collectForm();
    $("modal-text").textContent =
      `Дневной лимит: ${d.daily_limit} откликов. Бот откликнется на найденные вакансии с сопроводительным письмом (строки перекрасятся по статусу).`;
    // Таблицу НЕ очищаем — откликаемся на уже найденные, строки обновятся на месте.
    showModal(() => { api("/api/apply", d); setBusy("apply"); });
  };
  $("btn-stop").onclick = () => api("/api/stop");
  $("btn-responses").onclick = () => {
    $("resp-body").innerHTML = '<tr><td colspan="5" class="muted-cell">Загружаю…</td></tr>';
    _respStore.clear(); _respLoggedOut.clear();  // свежая выборка (в т.ч. по всем сайтам)
    _lastRespReq = Date.now();  // ручное обновление тоже считается за «обновили»
    _pendingManualResp = true;  // ручной запрос — таблицу перерисовать можно
    api("/api/responses");      // в режиме «все» веером по всем площадкам
    setBusy("responses");
  };
  $("btn-open").onclick = openSelected;
  $("btn-letter").onclick = () => {
    const tr = document.querySelector(".vac-table tr.selected");
    const v = tr && tr._vac;
    if (!v) { logLine("Выберите вакансию в таблице, чтобы сгенерировать письмо."); return; }
    logLine("Генерирую сопроводительное письмо для «" + (v.title || "") + "»…");
    api("/api/letter", { site: v.site || currentSite, id: v.id, url: v.url,
                         title: v.title, company: v.company, profession: v.profession });
    setBusy("letter");
  };
  // Модалка письма
  $("letter-close").onclick = () => { $("letter-modal").style.display = "none"; };
  $("letter-copy").onclick = async () => {
    const t = $("letter-text").value;
    try { await navigator.clipboard.writeText(t); }
    catch (e) { $("letter-text").select(); document.execCommand("copy"); }
    logLine("Письмо скопировано в буфер обмена.");
  };
  $("letter-modal").addEventListener("click", (e) => {
    if (e.target === $("letter-modal")) $("letter-modal").style.display = "none";
  });
  $("btn-show-browser").onclick = () => {
    logLine("Открываю окно браузера…");
    api("/api/show_browser");
  };
  $("btn-reconnect").onclick = () => {
    // Подвести к карточке подключения для сайта поиска и сбросить вход (смена аккаунта).
    selectConnectSite(currentSite);
    const card = $("connect-card");
    if (card) {
      card.classList.remove("collapsed");
      card.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    logLine("Выход из аккаунта " + (SITE_NAMES[currentSite] || currentSite) + " — введите данные заново.");
    api("/api/logout_site", { site: currentSite });
    togglePanelConnected(false);
  };
  $("all_pages").onchange = applyAllPagesState;
  // Кнопки способов входа строятся динамически (renderConnectModes) под каждый сайт.
  $("btn-connect").onclick = () => {
    const m = currentMethod();
    const f = m.fields || [];
    const username = $("hh-username").value.trim();
    if (f.includes("username") && !username) { logLine("Укажите логин (телефон или email)."); return; }
    const body = { site: connectSite, username };
    if (f.includes("password")) {
      const password = $("hh-password").value;
      if (!password) { logLine("Укажите пароль."); return; }
      body.password = password;
      logLine("Вхожу на " + (SITE_NAMES[connectSite] || connectSite) + "…");
    } else {
      // Вход по коду: без пароля -> сайт пришлёт код по SMS/письму.
      body.password = "";
      logLine("Отправляю запрос на код для " + username + "…");
    }
    api("/api/connect", body);
    setBusy("connect");
  };
  $("btn-manual-login").onclick = () => {
    logLine("Открываю окно браузера для входа на " + (SITE_NAMES[connectSite] || connectSite) + "…");
    api("/api/show_browser", { site: connectSite });
  };
  $("btn-relogin").onclick = () => {
    logLine("Выход из аккаунта " + (SITE_NAMES[connectSite] || connectSite) + " — войдите заново.");
    api("/api/logout_site", { site: connectSite });
    togglePanelConnected(false);
  };
  $("btn-send-sms").onclick = () => {
    const code = $("hh-sms").value.trim();
    if (!code) { logLine("Введите код из SMS/письма."); return; }
    logLine("Отправляю код подтверждения…");
    api("/api/sms", { site: connectSite, code });
    setBusy("submit_sms");
  };
  // Капча: кнопка «Пройти капчу» открывает видимое окно браузера на форме входа.
  $("captcha-cancel").onclick = hideCaptchaModal;
  $("captcha-open").onclick = () => {
    hideCaptchaModal();
    logLine("Открываю окно браузера для прохождения капчи…");
    api("/api/show_browser", { site: captchaSite || connectSite });
  };
  $("btn-disconnect").onclick = async () => {
    await api("/api/disconnect", { site: connectSite });
    $("hh-password").value = "";
    $("hh-sms").value = "";
    logLine("Аккаунт " + (SITE_NAMES[connectSite] || connectSite) + " отключён.");
    loadConnStatusFor(connectSite);
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
  async function saveConfig() {
    await api("/api/save", collectForm());
    $("autopilot-badge").style.display = $("autopilot_enabled").checked ? "" : "none";
    updateAutomationBadge();
    logLine($("autopilot_enabled").checked
      ? "Настройки сохранены. Автопилот включён."
      : "Настройки сохранены.");
  }
  $("btn-save").onclick = saveConfig;              // кнопка в «Критериях»
  $("btn-save-auto").onclick = saveConfig;         // кнопка в «Автоматизации и письме»
  // Живое обновление бейджа карточки при переключении автопилота/монитора.
  $("autopilot_enabled").addEventListener("change", updateAutomationBadge);
  $("monitor_enabled").addEventListener("change", updateAutomationBadge);

  $("salary_min").addEventListener("input", (e) => {
    const pos = e.target.value.length;
    e.target.value = groupDigits(e.target.value);
  });
}

// ---------- старт ----------
window.addEventListener("DOMContentLoaded", async () => {
  if (!requireAuth()) return;  // нет токена -> на /login
  applyTheme(currentTheme());  // применить сохранённую тему сразу
  $("btn-theme").onclick = toggleTheme;
  $("btn-logout").onclick = logout;
  try {
    const me = await (await authFetch("/auth/me")).json();
    $("user-email").textContent = me.email || "";
  } catch (e) { return; }  // 401 -> authFetch уже увёл на /login
  // Разрешение на системные уведомления (чтобы окно капчи всплывало поверх всего).
  try {
    if ("Notification" in window && Notification.permission === "default")
      Notification.requestPermission();
  } catch (e) { /* no-op */ }
  await loadSites();
  setStatus(false);  // применить режим (нейтральный для «все сайты»)
  await loadConfig();
  attachAutocomplete($("professions"), fetchProfessions, true);
  attachAutocomplete($("region"), fetchCities, false);
  bindButtons();
  bindModal();
  bindCollapsibles();
  connectEvents();
  // Статусы подключения площадок уже загружены в loadSites (бейджи + точки).
  loadProxy();                               // статус прокси пользователя
  loadTelegram();                            // статус Telegram-уведомлений
  loadStats();                               // дашборд статистики
  startResponsesAutoRefresh();               // авто-обновление ответов раз в N мин
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
    if (_awaitingCode && connectSite === currentSite) return;  // идёт ввод кода — не мешать
    if (Date.now() - lastCheck < 5000) return;
    lastCheck = Date.now();
    api("/api/check_login").catch(() => {});
  };
  document.addEventListener("visibilitychange", recheck);
  window.addEventListener("focus", recheck);
}
