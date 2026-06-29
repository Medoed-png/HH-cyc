"use strict";

// Общие помощники аутентификации: хранение JWT и авторизованные запросы.
// Подключается ПЕРЕД app.js. Токен живёт в localStorage.

const TOKEN_KEY = "hh_token";

function getToken() { return localStorage.getItem(TOKEN_KEY); }
function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
function clearToken() { localStorage.removeItem(TOKEN_KEY); }

function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  const t = getToken();
  if (t) h["Authorization"] = "Bearer " + t;
  return h;
}

// fetch с токеном; на 401 — чистим токен и уводим на страницу входа.
async function authFetch(path, options) {
  options = options || {};
  options.headers = authHeaders(options.headers);
  const r = await fetch(path, options);
  if (r.status === 401) {
    clearToken();
    if (location.pathname !== "/login") location.href = "/login";
    throw new Error("unauthorized");
  }
  return r;
}

// Если токена нет — на страницу входа (защита основной страницы на клиенте).
function requireAuth() {
  if (!getToken()) { location.href = "/login"; return false; }
  return true;
}

function logout() {
  authFetch("/auth/logout", { method: "POST" }).catch(() => {});
  clearToken();
  location.href = "/login";
}
