// Rota staff desk: calendar, clients, reminders, team, services, reports and settings.
// Talks to /api/desk/* as a signed-in staff member and keeps in sync with the server.
(() => {
'use strict';
document.documentElement.lang = 'en';

/* ================= helpers ================= */
const SLOT = 18; // px per quarter hour
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ESC[c]);
const pad = n => String(n).padStart(2, '0');
const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseISO = s => { const [y, m, d] = String(s).split('-').map(Number); return new Date(y, (m || 1) - 1, d || 1); };
const addDays = (s, n) => { const d = parseISO(s); d.setDate(d.getDate() + n); return iso(d); };
const todayISO = () => iso(new Date());
const nowMin = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); };
const nowISO = () => new Date().toISOString();
const hm = m => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
const hShort = m => m % 60 ? `${Math.floor(m / 60)}:${pad(m % 60)}` : String(m / 60);
const durTxt = m => m < 60 ? `${m} min` : m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`;
const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const WDL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MOL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const WEEK = [1, 2, 3, 4, 5, 6, 0];
const dow = s => parseISO(s).getDay();
const fmtDay = s => { const d = parseISO(s); return `${WD[d.getDay()]} ${d.getDate()} ${MO[d.getMonth()]}`; };
const fmtShort = s => { const d = parseISO(s); return `${d.getDate()} ${MO[d.getMonth()]}`; };
const fmtLong = s => { const d = parseISO(s); return `${WDL[d.getDay()]}, ${d.getDate()} ${MOL[d.getMonth()]}`; };
const relDay = s => { const t = todayISO(); return s === t ? 'Today' : s === addDays(t, 1) ? 'Tomorrow' : s === addDays(t, -1) ? 'Yesterday' : ''; };
const dayName = s => relDay(s) || fmtDay(s);
const digits = p => String(p ?? '').replace(/\D/g, '');
const samePhone = (a, b) => { const x = digits(a), y = digits(b); return x.length >= 6 && y.length >= 6 && x.slice(-8) === y.slice(-8); };
const uid = p => p + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
const first = n => String(n || '').trim().split(/\s+/)[0] || '';
const initials = n => String(n || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0] || '').join('').toUpperCase() || '?';
const pct = x => `${Math.round((x || 0) * 100)}%`;
const sum = (arr, f) => arr.reduce((s, x) => s + (+f(x) || 0), 0);
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

const STATUS = { booked: 'Awaiting', confirmed: 'Confirmed', arrived: 'Arrived', noshow: 'No-show', cancelled: 'Cancelled' };
const SOURCE = { online: 'Online booking', desk: 'At the desk', phone: 'Phone call' };
const DEFAULTS = {
  name: 'Your studio', tagline: '', address: '', phone: '', currency: '֏', countryCode: '374', defaultLang: 'hy', timezone: 'Asia/Yerevan', i18n: {},
  open: 600, close: 1200, step: 15, bookingDays: 14, leadMin: 30, cancelHours: 3,
  reminder: {
    en: 'Hi {name}! A reminder of your visit {when} at {time}: {service} with {specialist} at {studio}, {address}. To change or cancel: {link}',
    hy: 'Բարև, {name}։ Հիշեցնում ենք Ձեր այցի մասին՝ {when}, ժամը {time}․ {service}, մասնագետ՝ {specialist}, {studio}, {address}։ Փոխելու կամ չեղարկելու համար՝ {link}',
    ru: 'Здравствуйте, {name}! Напоминаем о визите {when} в {time}: {service}, мастер — {specialist}, {studio}, {address}. Перенести или отменить: {link}',
  },
};

const I = {
  cal: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.6c1.9.7 3.1 2.4 3.5 5.4"/>',
  scissors: '<circle cx="6" cy="7" r="3"/><circle cx="6" cy="17" r="3"/><path d="M8.6 8.6 20 19M8.6 15.4 20 5"/>',
  badge: '<rect x="4" y="3" width="16" height="18" rx="2.5"/><circle cx="12" cy="10" r="3"/><path d="M7.8 17.2c.7-1.9 2.2-3 4.2-3s3.5 1.1 4.2 3"/>',
  chart: '<path d="M4 20V11M10 20V5M16 20v-6M21.5 20h-19"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.3 2.4 3.5 5.2 3.5 8.5s-1.2 6.1-3.5 8.5c-2.3-2.4-3.5-5.2-3.5-8.5s1.2-6.1 3.5-8.5z"/>',
  sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  left: '<path d="m14.5 6-6 6 6 6"/>',
  right: '<path d="m9.5 6 6 6-6 6"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  chat: '<path d="M20.5 11.6a8.4 8.4 0 0 1-12.3 7.4L3.5 20.5l1.5-4.5a8.4 8.4 0 1 1 15.5-4.4z"/><path d="M8.5 10.5h7M8.5 13.5h4.5"/>',
  pin: '<path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
  spark: '<path d="M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
};
const icon = k => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[k]}</svg>`;

/* ================= state ================= */
const S = {
  view: 'calendar', date: todayISO(),
  settings: null, staff: [], services: [], clients: [], appts: [],
  ready: false, mode: 'connecting', pending: 0,
  user: null, rev: 0, login: { error: '', email: '' },
  drawer: null,
  cq: '', cf: 'all', cs: 'last', remRange: 'tomorrow',
  range: 14,
  scrolledFor: null,
};
const VIEWS = ['calendar', 'reminders', 'clients', 'services', 'team', 'reports', 'settings'];
{ const h = (location.hash || '').slice(1); if (VIEWS.includes(h)) S.view = h; }

const cfg = () => ({ ...DEFAULTS, ...(S.settings || {}) });
const trRaw = (doc, field, lang) => (doc && doc.i18n && doc.i18n[lang] && doc.i18n[lang][field]) || '';
const trFields = (seed, fields, prefix) => `<details class="tr"${['hy', 'ru'].some(l => fields.some(f => trRaw(seed, f, l))) ? ' open' : ''}><summary>Armenian and Russian versions</summary><div class="tr-grid">${['hy', 'ru'].map(l => fields.map(f => `<div class="field"><label for="${prefix}-${f}-${l}">${f[0].toUpperCase() + f.slice(1)} (${LANG_NAMES[l]})</label>${f === 'description' || f === 'bio' ? `<textarea id="${prefix}-${f}-${l}" name="${f}_${l}" rows="2">${esc(trRaw(seed, f, l))}</textarea>` : `<input id="${prefix}-${f}-${l}" name="${f}_${l}" value="${esc(trRaw(seed, f, l))}">`}</div>`).join('')).join('')}</div></details>`;
const readTr = (form, fields) => Object.fromEntries(['hy', 'ru'].map(l => [l, Object.fromEntries(fields.map(f => [f, (form.elements.namedItem(f + '_' + l) || {}).value?.trim() || '']).filter(([, v]) => v))]));
const staffById = id => S.staff.find(s => s.id === id);
const svcById = id => S.services.find(s => s.id === id);
const clientById = id => S.clients.find(c => c.id === id);
const colorCls = st => 'c' + (((st && st.color) || 0) % 6);
const sortedStaff = () => [...S.staff].sort((a, b) => (a.order ?? 99) - (b.order ?? 99) || String(a.name).localeCompare(b.name));
const sortedServices = () => [...S.services].sort((a, b) => (a.order ?? 999) - (b.order ?? 999) || String(a.name).localeCompare(b.name));
const svcNames = a => (a.serviceIds || []).map(id => svcById(id)?.name || 'Removed service').join(', ') || 'Appointment';
const money = n => { const c = cfg().currency || ''; const v = Math.round(+n || 0).toLocaleString('en-US'); return /^[$£€¥]$/.test(c) ? c + v : `${v} ${c}`; };
const shortMoney = n => { const c = cfg().currency || ''; const v = n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : String(Math.round(n)); return /^[$£€¥]$/.test(c) ? c + v : `${v} ${c}`; };
const isVisit = a => a.status !== 'cancelled' && a.status !== 'noshow' && (a.status === 'arrived' || a.paid > 0);
const isUpcoming = a => (a.status === 'booked' || a.status === 'confirmed') && (a.date > todayISO() || (a.date === todayISO() && a.start + a.duration > nowMin()));
const hoursOn = (st, date) => { const h = st && st.hours ? st.hours[dow(date)] : null; return Array.isArray(h) && h.length === 2 ? h : null; };
const groupBy = (arr, f) => { const m = new Map(); for (const x of arr) { const k = f(x); if (!m.has(k)) m.set(k, []); m.get(k).push(x); } return m; };

let statsMemo = { key: null, at: '', val: null };
function clientStats() {
  const at = todayISO() + nowMin();
  if (statsMemo.key === S.appts && statsMemo.at === at) return statsMemo.val;
  const m = new Map();
  for (const a of S.appts) {
    if (!a.clientId) continue;
    let s = m.get(a.clientId);
    if (!s) m.set(a.clientId, s = { visits: 0, spent: 0, last: null, next: null, noshow: 0, total: 0 });
    s.total++;
    if (a.status === 'noshow') s.noshow++;
    if (isVisit(a)) { s.visits++; if (!s.last || a.date > s.last) s.last = a.date; }
    s.spent += +a.paid || 0;
    if (isUpcoming(a)) { const k = a.date + hm(a.start); if (!s.next || k < s.next.k) s.next = { k, a }; }
  }
  statsMemo = { key: S.appts, at, val: m };
  return m;
}
const EMPTY_STATS = { visits: 0, spent: 0, last: null, next: null, noshow: 0, total: 0 };

/* ================= data layer ================= */
// The server is the source of truth. Writes update the screen at once and go to the API;
// every few seconds the desk asks for whatever changed since the last revision it saw.
const COLKEY = { appointments: 'appts', clients: 'clients', services: 'services', staff: 'staff' };
const clean = o => { const { id, manage, ...rest } = o; return JSON.parse(JSON.stringify(rest)); };

async function api(path, opts = {}) {
  const method = opts.method || 'GET';
  const headers = method === 'GET' ? {} : { 'X-Rota': '1' };
  if (opts.body) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { method, headers, body: opts.body ? JSON.stringify(opts.body) : undefined, credentials: 'same-origin' });
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const err = new Error((data && data.message) || res.statusText); err.status = res.status; err.code = data && data.error;
    if (res.status === 401 && S.user && !opts.quiet) signedOut();
    throw err;
  }
  return data;
}
function applyLocal(col, id, body) {
  if (col === 'settings') S.settings = { ...body };
  else {
    const k = COLKEY[col]; const prev = S[k].find(r => r.id === id);
    const row = { id, ...body, ...(prev && prev.manage ? { manage: prev.manage } : {}) };
    S[k] = prev ? S[k].map(r => (r.id === id ? row : r)) : [...S[k], row];
  }
  scheduleRender();
}
async function put(col, id, data) {
  const body = clean(data);
  applyLocal(col, id, body);
  S.pending++;
  try { await api(`/api/desk/${col}/${encodeURIComponent(id)}`, { method: 'PUT', body }); }
  finally { S.pending--; scheduleRender(); }
}
async function drop(col, id) {
  const k = COLKEY[col]; S[k] = S[k].filter(r => r.id !== id); scheduleRender();
  S.pending++;
  try { await api(`/api/desk/${col}/${encodeURIComponent(id)}`, { method: 'DELETE' }); }
  finally { S.pending--; scheduleRender(); }
}
function writeError(e) {
  if (e && e.status === 401) return;
  if (e && e.code === 'owner_only') toast('Only the studio owner can change this.');
  else if (e && e.status === 400) toast(`Not saved: ${e.message}`);
  else toast('That change did not save. Check your connection and try again.');
  sync(true);
}
async function save(col, id, data, msg) {
  try { await put(col, id, data); if (msg) toast(msg); return true; }
  catch (e) { writeError(e); return false; }
}
async function remove(col, id, msg) {
  try { await drop(col, id); if (msg) toast(msg); return true; }
  catch (e) { writeError(e); return false; }
}

let syncTimer = 0, syncing = false;
async function sync(full = false) {
  if (syncing || !S.user) return;
  syncing = true;
  try {
    const d = await api(`/api/desk/sync?rev=${full ? 0 : S.rev}`);
    const first = !S.ready || full;
    if (full) { S.settings = null; for (const k of Object.values(COLKEY)) S[k] = []; }
    const lists = {}; for (const k of Object.values(COLKEY)) lists[k] = S[k].slice();
    const changed = [];
    for (const r of d.rows) {
      if (r.kind === 'settings') { S.settings = r.deleted ? null : r.body; continue; }
      const k = COLKEY[r.kind]; if (!k) continue;
      const list = lists[k]; const i = list.findIndex(x => x.id === r.id);
      if (r.deleted) { if (i >= 0) list.splice(i, 1); continue; }
      const row = { id: r.id, ...r.body }; const prev = i >= 0 ? list[i] : null;
      if (i >= 0) list[i] = row; else list.push(row);
      if (k === 'appts' && !first) changed.push({ prev, row });
    }
    if (d.rows.length || first) for (const k of Object.values(COLKEY)) S[k] = lists[k];
    S.rev = d.rev; S.ready = true; S.mode = 'live';
    if (changed.length) announce(changed);
    if (d.rows.length || first) scheduleRender();
  } catch (e) {
    if (e.status !== 401 && S.mode !== 'offline') { S.mode = 'offline'; scheduleRender(); }
  } finally { syncing = false; }
}
function startSync() {
  clearInterval(syncTimer);
  sync(true);
  syncTimer = setInterval(() => { if (!document.hidden) sync(); }, 4000);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
function signedOut() { clearInterval(syncTimer); S.user = null; S.ready = false; S.mode = 'signedout'; S.drawer = null; closeDrawer(); render(); }
async function boot() {
  try { S.user = await api('/api/auth/me', { quiet: true }); }
  catch (e) { S.mode = e.status === 401 ? 'signedout' : 'offline'; render(); if (e.status !== 401) setTimeout(boot, 5000); return; }
  S.mode = 'connecting'; render(); startSync();
}

function announce(changes) {
  for (const { prev, row: a } of changes) {
    const open = { label: 'Open', fn: () => { S.date = a.date; go('calendar'); openDrawer({ kind: 'appt', id: a.id, live: true }); } };
    // Judge by what changed, not by timestamps: two client actions can land in the same second.
    const recent = a.clientActionAt && Date.now() - Date.parse(a.clientActionAt) < 5 * 60e3;
    if (prev && recent && a.status === 'cancelled' && a.cancelledBy === 'client' && prev.status !== 'cancelled') {
      toast(`${first(a.clientName)} cancelled their ${dayName(a.date)} ${hm(a.start)} visit online`, open); continue;
    }
    if (prev && recent && a.clientAction === 'rescheduled' && (prev.date !== a.date || prev.start !== a.start)) {
      toast(`${first(a.clientName)} moved their visit to ${dayName(a.date)} ${hm(a.start)}`, open); continue;
    }
    if (!prev && a.source === 'online' && a.createdAt && Date.now() - Date.parse(a.createdAt) < 5 * 60e3)
      toast(`New online booking: ${first(a.clientName)}, ${dayName(a.date)} ${hm(a.start)}`, open);
  }
}

/* ================= render infrastructure ================= */
let rafId = 0, deferred = false;
function scheduleRender() { if (rafId) return; rafId = requestAnimationFrame(() => { rafId = 0; render(); }); }

function preserve(root, key, fn) {
  const ae = document.activeElement;
  const id = ae && ae !== document.body && root.contains(ae) ? ae.id : null;
  let sel = null;
  if (id) { try { if (typeof ae.selectionStart === 'number') sel = [ae.selectionStart, ae.selectionEnd]; } catch (e) { sel = null; } }
  const sticky = $$('[data-sticky]', root).map(el => [el.id, el.value]);
  const scrolls = $$('[data-keep]', root).map(el => [el.dataset.keep, el.scrollTop, el.scrollLeft]);
  const same = root.dataset.key === key;
  const top = root.scrollTop;
  fn();
  root.dataset.key = key;
  if (same) {
    root.scrollTop = top;
    for (const [k, t, l] of scrolls) { const el = root.querySelector(`[data-keep="${k}"]`); if (el) { el.scrollTop = t; el.scrollLeft = l; } }
    for (const [sid, v] of sticky) { const el = sid && document.getElementById(sid); if (el) el.value = v; }
  }
  if (id) {
    const el = document.getElementById(id);
    if (el) { el.focus({ preventScroll: true }); if (sel) { try { el.setSelectionRange(sel[0], sel[1]); } catch (e) { /* not a text field */ } } }
  }
}

function render() {
  if (drag && drag.moved) { deferred = true; return; }
  const app = $('#app'), main = $('#main');
  const signedIn = !!S.user;
  app.classList.toggle('is-login', !signedIn);
  if (!signedIn) {
    $('#side').innerHTML = '';
    main.classList.remove('is-cal');
    preserve(main, 'login', () => { main.innerHTML = S.mode === 'signedout' ? loginHTML() : S.mode === 'offline' ? `<div class="login"><h1>Can't reach the server</h1><p>Retrying every few seconds.</p></div>` : loadingHTML('Opening…'); });
    return;
  }
  main.classList.toggle('is-cal', S.view === 'calendar' && S.ready && S.staff.length > 0);
  preserve($('#side'), 'side', () => { $('#side').innerHTML = sideHTML(); });
  preserve(main, S.view, () => { main.innerHTML = bannerHTML() + viewHTML(); });
  afterRender();
  if (S.drawer && S.drawer.live) renderDrawer(true);
}

function afterRender() {
  if (S.view === 'calendar' && S.ready) {
    const wrap = $('.cal-wrap');
    if (wrap && S.scrolledFor !== S.date) {
      S.scrolledFor = S.date;
      const c = cfg(); const day = S.appts.filter(a => a.date === S.date && a.status !== 'cancelled');
      const target = S.date === todayISO() ? nowMin() - 90 : (day.length ? Math.min(...day.map(a => a.start)) - 30 : c.open);
      wrap.scrollTop = Math.max(0, (target - c.open) / 15 * SLOT);
    }
  }
}

function viewHTML() {
  switch (S.view) {
    case 'clients': return viewClients();
    case 'services': return viewServices();
    case 'team': return viewTeam();
    case 'reports': return viewReports();
    case 'reminders': return viewReminders();
    case 'settings': return viewSettings();
    default: return viewCalendar();
  }
}

function bannerHTML() {
  if (S.mode === 'offline') return `<div class="banner" role="note">${icon('spark')}<span>Can't reach the server. Showing what was last loaded; changes may not save until it's back.</span></div>`;
  return '';
}

function loginHTML() {
  return `<form class="login" data-form="login" novalidate>
    <div class="brand"><span class="mark" aria-hidden="true">R</span><div class="brand-txt"><b>Rota</b><small>Studio desk</small></div></div>
    <div><h1>Sign in</h1><p>Use the email and password the studio owner set up for you.</p></div>
    <div class="field"><label for="li-email">Email</label><input id="li-email" name="email" type="email" autocomplete="username" value="${esc(S.login.email || '')}" required${S.login.email ? '' : ' autofocus'}></div>
    <div class="field"><label for="li-pw">Password</label><input id="li-pw" name="password" type="password" autocomplete="current-password" required${S.login.email ? ' autofocus' : ''}></div>
    <p class="form-err" id="li-err" role="alert">${esc(S.login.error)}</p>
    <button class="btn primary" id="li-go" style="height:46px">Sign in</button>
  </form>`;
}

const loadingHTML = msg => `<div class="loading">${esc(msg)}</div>`;

function sideHTML() {
  const c = cfg(); const t = todayISO();
  const todayN = S.appts.filter(a => a.date === t && a.status !== 'cancelled').length;
  const items = [
    ['calendar', 'cal', 'Calendar', S.ready ? todayN : ''],
    ['reminders', 'chat', 'Reminders', S.ready ? (dueReminders() || '') : ''],
    ['clients', 'users', 'Clients', S.ready ? S.clients.length : ''],
    ['services', 'scissors', 'Services', ''],
    ['team', 'badge', 'Team', ''],
    ['reports', 'chart', 'Reports', ''],
    ['settings', 'sliders', 'Settings', ''],
  ];
  const sync = {
    connecting: ['', 'Connecting…'],
    live: ['live', S.pending ? 'Saving…' : 'Live · synced'],
    offline: ['offline', 'Offline · retrying'],
  }[S.mode] || ['', ''];
  return `<div class="brand"><span class="mark" aria-hidden="true">R</span><div class="brand-txt"><b>Rota</b><small>${esc(c.name)}</small></div></div>
  <nav class="nav" aria-label="Sections">${items.map(([v, ic, label, n]) => `<button type="button" id="nav-${v}" data-act="nav" data-view="${v}"${S.view === v ? ' aria-current="page"' : ''}>${icon(ic)}<span>${label}</span>${n !== '' ? `<span class="count">${n}</span>` : ''}</button>`).join('')}<a href="/" target="_blank" rel="noopener" id="nav-site">${icon('globe')}<span>Booking site</span></a></nav>
  <div class="side-foot"><span class="sync ${sync[0]}"><i></i>${sync[1]}</span>${c.sample ? '<span class="chip">Sample data</span>' : ''}
    <div class="me-row"><b title="${esc(S.user.email)}">${esc(S.user.name || S.user.email)}</b><button type="button" class="btn ghost sm" id="logout" data-act="logout">Sign out</button></div></div>`;
}

/* ================= calendar ================= */
function layoutLanes(list) {
  const items = [...list].sort((a, b) => a.start - b.start || b.duration - a.duration).map(a => ({ a, lane: 0, of: 1 }));
  const out = []; let cluster = []; let end = -1;
  const flush = () => {
    const ends = [];
    for (const it of cluster) {
      let i = ends.findIndex(e => e <= it.a.start);
      if (i < 0) { i = ends.length; ends.push(0); }
      ends[i] = it.a.start + it.a.duration; it.lane = i;
    }
    for (const it of cluster) { it.of = ends.length; out.push(it); }
    cluster = []; end = -1;
  };
  for (const it of items) {
    if (cluster.length && it.a.start >= end) flush();
    cluster.push(it); end = Math.max(end, it.a.start + it.a.duration);
  }
  if (cluster.length) flush();
  return out;
}

function weekHTML(d) {
  const mon = addDays(d, -((dow(d) + 6) % 7)); const t = todayISO();
  return `<div class="week" role="group" aria-label="This week">${Array.from({ length: 7 }, (_, i) => {
    const s = addDays(mon, i); const dd = parseISO(s);
    const list = S.appts.filter(a => a.date === s && a.status !== 'cancelled' && a.status !== 'noshow');
    const work = sum(S.staff, st => { const h = hoursOn(st, s); return h ? h[1] - h[0] : 0; });
    const load = work ? Math.min(1, sum(list, a => a.duration) / work) : 0;
    return `<button type="button" class="wk${s === t ? ' today' : ''}" id="wk-${i}" data-act="goto" data-date="${s}"${s === d ? ' aria-current="date"' : ''} aria-label="${esc(fmtLong(s))}, ${plural(list.length, 'appointment')}"><span class="d">${WD[dd.getDay()]}</span><span class="c">${list.length || '–'}</span><span class="n">${dd.getDate()}</span><span class="meter"><i style="width:${Math.round(load * 100)}%"></i></span></button>`;
  }).join('')}</div>`;
}

function viewCalendar() {
  if (!S.ready) return loadingHTML('Opening the appointment book…');
  const c = cfg(), d = S.date, t = todayISO();
  const staff = sortedStaff();
  const live = S.appts.filter(a => a.date === d && a.status !== 'cancelled');
  const active = live.filter(a => a.status !== 'noshow');
  const work = sum(staff, s => { const h = hoursOn(s, d); return h ? h[1] - h[0] : 0; });
  const load = work ? sum(active, a => a.duration) / work : 0;
  const rel = relDay(d);
  const head = `<div class="cal-bar">
    <div class="cal-row">
      <div class="cal-nav">
        <button type="button" class="iconbtn" id="cal-prev" data-act="day" data-n="-1" aria-label="Previous day" title="Previous day (←)">${icon('left')}</button>
        <button type="button" class="iconbtn" id="cal-next" data-act="day" data-n="1" aria-label="Next day" title="Next day (→)">${icon('right')}</button>
      </div>
      <h1 class="cal-title">${esc(fmtLong(d))}${rel ? ` <span class="rel">${rel}</span>` : ''}</h1>
      <span class="spacer"></span>
      ${d !== t ? '<button type="button" class="btn sm" id="cal-today" data-act="today" title="Today (T)">Today</button>' : ''}
      <label class="datepick"><span class="sr">Go to date</span><input type="date" id="cal-date" value="${d}"></label>
      <button type="button" class="btn primary newappt" id="cal-new" data-act="new-appt" aria-label="New appointment">${icon('plus')}<span>New appointment</span></button>
    </div>
    ${weekHTML(d)}
    <div class="cal-sum" aria-label="Day summary">
      <span>Appointments<b>${live.length}</b></span>
      <span>Booked value<b>${money(sum(active, a => a.price))}</b></span>
      <span>Paid<b>${money(sum(live, a => a.paid))}</b></span>
      <span>Load<b>${pct(load)}</b></span>
      <span>Online<b>${live.filter(a => a.source === 'online').length}</b></span>
      <span class="cal-hint">Click a free slot to book · drag a booking to move it · ← → to change day</span>
    </div>
  </div>`;
  if (!staff.length) {
    return head + `<div class="empty"><h2>No specialists yet</h2><p>The calendar has one column per specialist. Add your team and their working hours to start taking appointments.</p><button type="button" class="btn primary" data-act="new-staff">Add a specialist</button></div>`;
  }
  const rows = Math.max(4, Math.round((c.close - c.open) / 15));
  const H = rows * SLOT;
  const y = m => (m - c.open) / 15 * SLOT;
  const hoff = ((60 - (c.open % 60)) % 60) / 15 * SLOT;
  const times = [];
  for (let m = Math.ceil(c.open / 60) * 60; m <= c.close; m += 60) times.push(`<span style="top:${y(m)}px">${hm(m)}</span>`);
  const nm = nowMin();
  const showNow = d === t && nm >= c.open && nm <= c.close;
  const heads = staff.map(s => {
    const h = hoursOn(s, d); const n = active.filter(a => a.staffId === s.id).length;
    return `<div class="cal-head"><span class="av ${colorCls(s)}">${esc(initials(s.name))}</span><div class="who"><b>${esc(s.name)}</b><small>${h ? `${hm(h[0])}–${hm(h[1])} · ${plural(n, 'appt')}` : 'Day off'}</small></div></div>`;
  }).join('');
  const cols = staff.map(s => {
    const h = hoursOn(s, d); const off = [];
    if (!h) off.push(`<div class="offhrs full" style="top:0;height:${H}px">Day off</div>`);
    else {
      if (h[0] > c.open) off.push(`<div class="offhrs" style="top:0;height:${Math.min(H, y(h[0]))}px"></div>`);
      if (h[1] < c.close) { const top = Math.max(0, y(h[1])); off.push(`<div class="offhrs" style="top:${top}px;height:${H - top}px"></div>`); }
    }
    const blocks = layoutLanes(live.filter(a => a.staffId === s.id)).map(it => apptBlock(it, y, H)).join('');
    return `<div class="cal-col" data-staff="${esc(s.id)}">${off.join('')}${blocks}${showNow ? `<div class="now" style="top:${y(nm)}px"></div>` : ''}</div>`;
  }).join('');
  return head + `<div class="cal-wrap" data-keep="cal"><div class="cal-grid" style="--cols:${staff.length};--rows:${rows};--slot:${SLOT}px;--hoff:${hoff}px">
    <div class="cal-corner"></div>${heads}
    <div class="cal-times" aria-hidden="true">${times.join('')}${showNow ? `<span class="now-label" style="top:${y(nm)}px">${hm(nm)}</span>` : ''}</div>${cols}
  </div></div>`;
}

function apptBlock({ a, lane, of }, y, H) {
  const top = Math.max(0, y(a.start)); const bot = Math.min(H, y(a.start + a.duration));
  const h = Math.max(SLOT, bot - top);
  const tags = [];
  if (a.paid > 0) tags.push('Paid');
  if (a.status === 'confirmed') tags.push('Confirmed');
  if (a.status === 'noshow') tags.push('No-show');
  if (a.source === 'online') tags.push('Online');
  const pos = of > 1 ? `left:calc(${lane} * 100% / ${of});width:calc(100% / ${of} - 6px);` : 'left:0;right:0;';
  const label = `${hm(a.start)} to ${hm(a.start + a.duration)}, ${a.clientName || 'Walk-in'}, ${svcNames(a)}, ${STATUS[a.status] || ''}`;
  const t = todayISO(); const past = a.date < t || (a.date === t && a.start + a.duration <= nowMin());
  const size = h < 34 ? ' tiny' : h < 62 ? ' compact' : '';
  return `<button type="button" class="appt ${colorCls(staffById(a.staffId))} s-${esc(a.status)}${size}${a.paid > 0 ? ' paid' : ''}${past ? ' past' : ''}" data-act="open-appt" data-id="${esc(a.id)}" style="top:${top}px;height:${h - 2}px;${pos}" aria-label="${esc(label)}">
    <span class="a1"><span class="t">${hm(a.start)}</span> <b>${esc(a.clientName || 'Walk-in')}</b></span>
    <span class="a2">${esc(svcNames(a))}</span>
    ${tags.length ? `<span class="a3">${tags.map(x => `<i>${x}</i>`).join('')}</span>` : ''}
  </button>`;
}

function defaultStart(date) {
  const c = cfg();
  if (date === todayISO()) { const m = Math.ceil(nowMin() / 15) * 15; if (m >= c.open && m < c.close) return m; }
  return c.open;
}

/* ================= clients ================= */
function viewClients() {
  if (!S.ready) return loadingHTML('Loading clients…');
  const st = clientStats(); const t = todayISO();
  const q = S.cq.trim().toLowerCase(); const qd = digits(q);
  const all = S.clients.map(c => ({ c, s: st.get(c.id) || EMPTY_STATS }));
  const lapsedCut = addDays(t, -45);
  const F = {
    all: () => true,
    regular: r => r.s.visits >= 3,
    new: r => r.s.visits <= 1,
    lapsed: r => r.s.last && r.s.last < lapsedCut && !r.s.next,
    noshow: r => r.s.noshow > 0,
  };
  const labels = { all: 'All', regular: 'Regulars', new: 'New', lapsed: 'Lapsed 45+ days', noshow: 'With no-shows' };
  let rows = all.filter(r => !q || String(r.c.name).toLowerCase().includes(q) || (qd.length >= 3 && digits(r.c.phone).includes(qd)));
  const counts = Object.fromEntries(Object.keys(F).map(k => [k, rows.filter(F[k]).length]));
  rows = rows.filter(F[S.cf] || F.all);
  const sorters = {
    last: (a, b) => String(b.s.last || '').localeCompare(String(a.s.last || '')),
    name: (a, b) => String(a.c.name).localeCompare(b.c.name),
    visits: (a, b) => b.s.visits - a.s.visits,
    spent: (a, b) => b.s.spent - a.s.spent,
  };
  rows.sort(sorters[S.cs] || sorters.last);
  const th = (k, label, cls = '') => `<th class="${cls}"><button type="button" data-act="client-sort" data-k="${k}" aria-pressed="${S.cs === k}">${label}${S.cs === k ? ' ↓' : ''}</button></th>`;
  return `<div class="page">
    <div class="pagehead"><div><h1>Clients</h1><p>Every visit, payment and no-show in one profile per client.</p></div>
      <div class="acts"><button type="button" class="btn primary" data-act="new-client">${icon('plus')}New client</button></div></div>
    <div class="toolbar">
      <div class="search">${icon('search')}<label class="sr" for="client-q">Search clients</label><input type="search" id="client-q" placeholder="Search by name or phone" value="${esc(S.cq)}" autocomplete="off"></div>
      <div class="seg" role="group" aria-label="Filter">${Object.keys(F).map(k => `<button type="button" id="cf-${k}" data-act="client-filter" data-f="${k}" aria-pressed="${S.cf === k}">${labels[k]} <span class="mono muted">${counts[k]}</span></button>`).join('')}</div>
    </div>
    ${S.clients.length === 0 ? `<div class="empty"><h2>No clients yet</h2><p>Clients appear here when you book them at the desk or when they book online.</p></div>` :
    rows.length === 0 ? `<div class="empty"><h2>No matches</h2><p>No client matches “${esc(S.cq)}” with this filter.</p></div>` :
    `<div class="tbl-wrap"><table class="tbl">
      <thead><tr>${th('name', 'Client')}<th class="hide-sm">Phone</th>${th('visits', 'Visits', 'num')}${th('last', 'Last visit')}<th class="hide-sm">Next</th>${th('spent', 'Spent', 'num')}<th class="num hide-sm">No-shows</th></tr></thead>
      <tbody>${rows.map(({ c, s }) => `<tr data-act="open-client" data-id="${esc(c.id)}">
        <td><div class="who-cell"><span class="av sm">${esc(initials(c.name))}</span><div><button type="button" class="linkish" data-act="open-client" data-id="${esc(c.id)}"><b>${esc(c.name)}</b></button>${c.notes ? `<small>${esc(c.notes)}</small>` : ''}</div></div></td>
        <td class="mono hide-sm">${esc(c.phone || '—')}</td>
        <td class="num">${s.visits}</td>
        <td>${s.last ? esc(fmtShort(s.last)) : '<span class="muted">—</span>'}</td>
        <td class="hide-sm">${s.next ? `${esc(dayName(s.next.a.date))} <span class="mono">${hm(s.next.a.start)}</span>` : '<span class="muted">—</span>'}</td>
        <td class="num">${money(s.spent)}</td>
        <td class="num hide-sm${s.noshow ? ' bad' : ''}">${s.noshow || '—'}</td>
      </tr>`).join('')}</tbody></table></div>`}
  </div>`;
}

/* ================= services ================= */
function viewServices() {
  if (!S.ready) return loadingHTML('Loading services…');
  const list = sortedServices();
  const cats = groupBy(list, s => s.category || 'Other');
  return `<div class="page">
    <div class="pagehead"><div><h1>Services</h1><p>What you offer, how long it takes and who performs it. Online booking uses these durations to find free time.</p></div>
      <div class="acts"><button type="button" class="btn primary" data-act="new-service">${icon('plus')}New service</button></div></div>
    ${!list.length ? `<div class="empty"><h2>No services yet</h2><p>Add the services clients can book, with their duration and price.</p><button type="button" class="btn primary" data-act="new-service">Add a service</button></div>` :
    `<div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Service</th><th class="num">Duration</th><th class="num">Price</th><th>Specialists</th><th>Online</th></tr></thead>
      <tbody>${[...cats].map(([cat, items]) => `<tr class="grp"><th colspan="5">${esc(cat)}</th></tr>` + items.map(s => {
        const who = sortedStaff().filter(st => (st.services || []).includes(s.id));
        return `<tr data-act="edit-service" data-id="${esc(s.id)}">
          <td><button type="button" class="linkish" data-act="edit-service" data-id="${esc(s.id)}"><b>${esc(s.name)}</b></button>${s.description ? `<small class="muted" style="display:block;max-width:44ch;white-space:normal">${esc(s.description)}</small>` : ''}</td>
          <td class="num">${durTxt(s.duration)}</td><td class="num">${money(s.price)}</td>
          <td>${who.length ? `<span class="stack">${who.map(st => `<span class="av sm ${colorCls(st)}" title="${esc(st.name)}">${esc(initials(st.name))}</span>`).join('')}</span>` : '<span class="muted">Nobody yet</span>'}</td>
          <td>${s.online === false ? '<span class="pill">Desk only</span>' : '<span class="pill s-confirmed">Bookable</span>'}</td>
        </tr>`;
      }).join('')).join('')}</tbody></table></div>`}
  </div>`;
}

/* ================= team ================= */
function viewTeam() {
  if (!S.ready) return loadingHTML('Loading the team…');
  const t = todayISO(); const mon = addDays(t, -((dow(t) + 6) % 7));
  const week = Array.from({ length: 7 }, (_, i) => addDays(mon, i));
  const staff = sortedStaff();
  return `<div class="page">
    <div class="pagehead"><div><h1>Team</h1><p>Working hours shape the calendar and the times clients can book online. Figures cover this week, ${esc(fmtShort(week[0]))} to ${esc(fmtShort(week[6]))}.</p></div>
      <div class="acts"><button type="button" class="btn primary" data-act="new-staff">${icon('plus')}New specialist</button></div></div>
    ${!staff.length ? `<div class="empty"><h2>No specialists yet</h2><p>Add the people who take appointments.</p><button type="button" class="btn primary" data-act="new-staff">Add a specialist</button></div>` :
    `<div class="cards">${staff.map(s => {
      const list = S.appts.filter(a => a.staffId === s.id && week.includes(a.date) && a.status !== 'cancelled');
      const active = list.filter(a => a.status !== 'noshow');
      const work = sum(week, d => { const h = hoursOn(s, d); return h ? h[1] - h[0] : 0; });
      const svc = sortedServices().filter(x => (s.services || []).includes(x.id));
      return `<article class="card ${colorCls(s)}">
        <div class="card-top"><span class="av lg ${colorCls(s)}">${esc(initials(s.name))}</span><div class="who"><h3>${esc(s.name)}</h3><small>${esc(s.role || 'Specialist')}</small></div>
          <button type="button" class="btn sm" data-act="edit-staff" data-id="${esc(s.id)}">Edit</button></div>
        <div class="sched" aria-label="Weekly hours">${WEEK.map(w => { const h = s.hours && s.hours[w]; return h ? `<div><b>${WD[w]}</b>${hShort(h[0])}–${hShort(h[1])}</div>` : `<div class="x"><b>${WD[w]}</b>off</div>`; }).join('')}</div>
        <dl class="stats"><div><dt>Appointments</dt><dd>${active.length}</dd></div><div><dt>Load</dt><dd>${work ? pct(sum(active, a => a.duration) / work) : '—'}</dd></div><div><dt>Paid</dt><dd>${shortMoney(sum(list, a => a.paid))}</dd></div></dl>
        <div class="tags">${svc.length ? svc.map(x => `<span class="tagx">${esc(x.name)}</span>`).join('') : '<span class="muted">No services assigned</span>'}</div>
      </article>`;
    }).join('')}</div>`}
  </div>`;
}

/* ================= reports ================= */
function metrics(days) {
  const set = new Set(days);
  const list = S.appts.filter(a => set.has(a.date) && a.status !== 'cancelled');
  const shown = list.filter(a => a.status !== 'noshow');
  const revenue = sum(list, a => a.paid);
  const paidN = list.filter(a => a.paid > 0).length;
  let work = 0;
  for (const d of days) for (const st of S.staff) { const h = hoursOn(st, d); if (h) work += h[1] - h[0]; }
  return {
    list, shown, count: list.length, revenue,
    avg: paidN ? revenue / paidN : 0,
    noshowRate: list.length ? (list.length - shown.length) / list.length : 0,
    onlineShare: list.length ? list.filter(a => a.source === 'online').length / list.length : 0,
    load: work ? sum(shown, a => a.duration) / work : 0,
  };
}
function deltaHTML(cur, prev, hasPrev, { pts = false, invert = false } = {}) {
  if (!hasPrev) return '<span class="delta">no earlier data</span>';
  let txt, dir;
  if (pts) { const dv = Math.round((cur - prev) * 100); dir = Math.sign(dv); txt = `${Math.abs(dv)} pts`; }
  else { if (!prev) return '<span class="delta">no earlier data</span>'; const r = (cur - prev) / prev; const dv = Math.round(r * 100); dir = Math.sign(dv); txt = `${Math.abs(dv)}%`; }
  const good = invert ? dir < 0 : dir > 0;
  return `<span class="delta ${dir === 0 ? '' : good ? 'up' : 'down'}">${dir > 0 ? '▲' : dir < 0 ? '▼' : '='} ${txt}</span>`;
}
function niceStep(v) { const e = Math.pow(10, Math.floor(Math.log10(v))); const f = v / e; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e; }

function viewReports() {
  if (!S.ready) return loadingHTML('Crunching the numbers…');
  const n = S.range; const t = todayISO();
  const days = Array.from({ length: n }, (_, i) => addDays(t, i - n + 1));
  const prevDays = days.map(d => addDays(d, -n));
  const m = metrics(days), p = metrics(prevDays); const hp = p.count > 0;
  const tiles = [
    ['Revenue', money(m.revenue), deltaHTML(m.revenue, p.revenue, hp)],
    ['Appointments', m.count, deltaHTML(m.count, p.count, hp)],
    ['Average ticket', money(m.avg), deltaHTML(m.avg, p.avg, hp)],
    ['Load', pct(m.load), deltaHTML(m.load, p.load, hp, { pts: true })],
    ['No-show rate', pct(m.noshowRate), deltaHTML(m.noshowRate, p.noshowRate, hp, { pts: true, invert: true })],
    ['Booked online', pct(m.onlineShare), deltaHTML(m.onlineShare, p.onlineShare, hp, { pts: true })],
  ];
  // daily revenue chart
  const daily = days.map(d => { const l = m.list.filter(a => a.date === d); return { d, rev: sum(l, a => a.paid), n: l.length }; });
  const maxV = Math.max(...daily.map(x => x.rev), 0);
  const step = maxV > 0 ? niceStep(maxV / 4) : 1;
  const top = maxV > 0 ? step * Math.ceil(maxV / step) : 4;
  const ticks = []; for (let v = 0; v <= top + 1e-9; v += step) ticks.push(v);
  const every = n <= 14 ? 1 : 3;
  const total = `${money(m.revenue)} over ${n} days`;
  const chart = `<div class="readout" id="readout" data-def="${esc(total)}">${esc(total)}</div>
    <div class="chart" style="--n:${n}">
      ${ticks.map(v => `<div class="gl${v === 0 ? ' base' : ''}" style="bottom:${v / top * 100}%"><span>${v === 0 ? '0' : shortMoney(v)}</span></div>`).join('')}
      <div class="cols">${daily.map(x => `<div class="col${x.d === t ? ' today' : ''}" data-tip="${esc(`${fmtDay(x.d)} · ${money(x.rev)} · ${plural(x.n, 'appointment')}`)}"><div class="bar" style="height:${x.rev / top * 100}%"></div></div>`).join('')}</div>
    </div>
    <div class="xl" style="--n:${n}" aria-hidden="true">${daily.map((x, i) => `<span>${(n - 1 - i) % every === 0 ? parseISO(x.d).getDate() : ''}</span>`).join('')}</div>`;
  // staff performance
  const staff = sortedStaff();
  const perf = staff.map(s => {
    const l = m.list.filter(a => a.staffId === s.id); const sh = l.filter(a => a.status !== 'noshow');
    let work = 0; for (const d of days) { const h = hoursOn(s, d); if (h) work += h[1] - h[0]; }
    const rev = sum(l, a => a.paid); const paidN = l.filter(a => a.paid > 0).length;
    return { s, n: l.length, rev, avg: paidN ? rev / paidN : 0, load: work ? sum(sh, a => a.duration) / work : 0, ns: l.length - sh.length };
  });
  // services
  const svcRows = new Map();
  for (const a of m.list) {
    const ids = a.serviceIds || []; const prices = ids.map(id => svcById(id)?.price || 0); const tot = prices.reduce((x, y) => x + y, 0);
    ids.forEach((id, i) => { const r = svcRows.get(id) || { n: 0, rev: 0 }; r.n++; r.rev += tot ? (+a.paid || 0) * prices[i] / tot : (+a.paid || 0) / ids.length; svcRows.set(id, r); });
  }
  const topSvc = [...svcRows].map(([id, r]) => ({ s: svcById(id), ...r })).filter(x => x.s).sort((a, b) => b.rev - a.rev || b.n - a.n).slice(0, 7);
  const maxSvc = Math.max(1, ...topSvc.map(x => x.rev));
  const src = ['online', 'desk', 'phone'].map(k => ({ k, n: m.list.filter(a => (a.source || 'desk') === k).length }));
  const srcTotal = Math.max(1, sum(src, x => x.n));
  return `<div class="page">
    <div class="pagehead"><div><h1>Reports</h1><p>The last ${n} days, ${esc(fmtShort(days[0]))} to ${esc(fmtShort(t))}, compared with the ${n} days before. Revenue counts payments taken.</p></div>
      <div class="seg" role="group" aria-label="Period">${[7, 14, 30].map(k => `<button type="button" id="rr-${k}" data-act="report-range" data-n="${k}" aria-pressed="${S.range === k}">${k} days</button>`).join('')}</div></div>
    <div class="tiles">${tiles.map(([k, v, dl]) => `<div class="tile"><span class="k">${k}</span><span class="v">${v}</span>${dl}</div>`).join('')}</div>
    <div class="grid2">
      <section class="box"><div class="box-head"><h2>Revenue by day</h2></div>${chart}</section>
      <section class="box"><div class="box-head"><h2>Load by specialist</h2><span class="muted">booked time ÷ working time</span></div>
        <div class="hbars">${perf.map(x => `<div class="hb ${colorCls(x.s)}"><span class="nm">${esc(x.s.name)}</span><span class="track"><span class="fill" style="display:block;width:${Math.min(100, Math.round(x.load * 100))}%"></span></span><span class="num">${pct(x.load)}</span></div>`).join('') || '<p class="muted">No specialists yet.</p>'}</div>
        <div class="box-head" style="margin-top:8px"><h2>Where bookings come from</h2></div>
        <div class="split" aria-hidden="true">${src.map(x => x.n ? `<i class="src-${x.k}" style="width:${x.n / srcTotal * 100}%"></i>` : '').join('')}</div>
        <div class="legend">${src.map(x => `<span><i class="src-${x.k}"></i>${SOURCE[x.k]} <b class="mono">${pct(x.n / srcTotal)}</b></span>`).join('')}</div>
      </section>
    </div>
    <div class="grid2">
      <section class="box"><div class="box-head"><h2>Specialists</h2></div>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Specialist</th><th class="num">Appts</th><th class="num">Revenue</th><th class="num">Avg ticket</th><th class="num">Load</th><th class="num">No-shows</th></tr></thead>
        <tbody>${perf.map(x => `<tr><td><div class="who-cell"><span class="av sm ${colorCls(x.s)}">${esc(initials(x.s.name))}</span>${esc(x.s.name)}</div></td><td class="num">${x.n}</td><td class="num">${money(x.rev)}</td><td class="num">${money(x.avg)}</td><td class="num">${pct(x.load)}</td><td class="num${x.ns ? ' bad' : ''}">${x.ns || '—'}</td></tr>`).join('')}</tbody></table></div>
      </section>
      <section class="box"><div class="box-head"><h2>Top services</h2><span class="muted">by revenue</span></div>
        <div class="hbars">${topSvc.length ? topSvc.map(x => `<div class="hb"><span class="nm">${esc(x.s.name)}</span><span class="track"><span class="fill" style="display:block;width:${Math.round(x.rev / maxSvc * 100)}%"></span></span><span class="num">${x.n}×</span></div>`).join('') : '<p class="muted">No appointments in this period.</p>'}</div>
      </section>
    </div>
  </div>`;
}

/* ================= WhatsApp reminders ================= */
// wa.me needs the number in international form, digits only.
function waNumber(phone) {
  const raw = String(phone || '').trim(); let d = digits(raw);
  if (!d) return '';
  const cc = digits(cfg().countryCode);
  if (raw.startsWith('+')) { /* already international */ }
  else if (d.startsWith('00')) d = d.slice(2);
  else if (d.startsWith('0')) { if (!cc) return ''; d = cc + d.replace(/^0+/, ''); }
  else if (cc && d.length <= 9) d = cc + d;
  return d.length >= 10 && d.length <= 15 ? d : '';
}
const apptPhone = a => a.clientPhone || (clientById(a.clientId) || {}).phone || '';
const waLink = a => { const n = waNumber(apptPhone(a)); return n ? `https://wa.me/${n}?text=${encodeURIComponent(reminderText(a))}` : ''; };
const remKey = a => `${a.date} ${a.start} ${a.staffId}`;
const remindable = a => (a.status === 'booked' || a.status === 'confirmed') && (a.date > todayISO() || (a.date === todayISO() && a.start > nowMin()));
const remState = a => !a.remindedAt ? 'todo' : a.remindedFor && a.remindedFor !== remKey(a) ? 'stale' : 'sent';
const sentAt = a => { const d = new Date(a.remindedAt); return `${relDay(iso(d)) || fmtShort(iso(d))}, ${hm(d.getHours() * 60 + d.getMinutes())}`; };
const dueReminders = () => { const tm = addDays(todayISO(), 1); return S.appts.filter(a => a.date === tm && remindable(a) && remState(a) !== 'sent').length; };

function waButton(a, sm = true) {
  const link = waLink(a);
  if (!link) return `<button type="button" class="btn${sm ? ' sm' : ''}" data-act="edit-appt-id" data-id="${esc(a.id)}">Add a phone number</button>`;
  return `<a class="btn wa${sm ? ' sm' : ''}" href="${esc(link)}" target="_blank" rel="noopener noreferrer" data-act="wa-send" data-id="${esc(a.id)}">${icon('chat')}${remState(a) === 'todo' ? 'Send on WhatsApp' : 'Send again'}</a>`;
}
function waStatusLine(a) {
  const st = remState(a);
  if (st === 'sent') return `<div class="wa-line sent">${icon('check')}Sent on WhatsApp ${esc(sentAt(a))}</div>`;
  if (st === 'stale') return `<div class="wa-line stale">Sent ${esc(sentAt(a))}, before the time changed. Send the new time.</div>`;
  return `<div class="wa-line todo">Not sent yet.</div>`;
}

function viewReminders() {
  if (!S.ready) return loadingHTML('Loading reminders…');
  const t = todayISO();
  const ranges = { today: [t], tomorrow: [addDays(t, 1)], week: Array.from({ length: 7 }, (_, i) => addDays(t, i + 1)) };
  const pick = k => S.appts.filter(a => ranges[k].includes(a.date) && remindable(a));
  const unsent = k => pick(k).filter(a => remState(a) !== 'sent').length;
  const list = pick(S.remRange).sort((a, b) => (a.date + hm(a.start)).localeCompare(b.date + hm(b.start)));
  const todo = list.filter(a => remState(a) !== 'sent'); const sent = list.filter(a => remState(a) === 'sent');
  const labels = { today: 'Later today', tomorrow: 'Tomorrow', week: 'Next 7 days' };
  const row = a => {
    const st = staffById(a.staffId); const state = remState(a);
    const stateTxt = state === 'sent' ? `Sent ${esc(sentAt(a))}` : state === 'stale' ? 'Time changed' : (waNumber(apptPhone(a)) ? 'Not sent' : 'No phone');
    return `<div class="rem-row">
      <div class="rem-when"><b>${hm(a.start)}</b><small>${esc(dayName(a.date))}</small></div>
      <div class="rem-who"><span class="av sm ${colorCls(st)}" title="${esc(st ? st.name : '')}">${esc(initials(st ? st.name : '?'))}</span><div class="txt"><button type="button" class="linkish" data-act="open-appt" data-id="${esc(a.id)}"><b>${esc(a.clientName || 'Client')}</b></button><small>${esc(svcNames(a))} · ${esc(apptPhone(a) || 'no phone')}</small></div></div>
      <div class="rem-acts"><span class="rem-state ${state}">${stateTxt}</span>${a.status === 'confirmed' ? '<span class="pill s-confirmed">Confirmed</span>' : ''}${state === 'sent' && a.status === 'booked' ? `<button type="button" class="btn sm" data-act="rem-confirm" data-id="${esc(a.id)}">Client confirmed</button>` : ''}${state !== 'sent' ? waButton(a) : ''}</div>
    </div>`;
  };
  const sample = list[0] || S.appts.find(a => remindable(a));
  return `<div class="page">
    <div class="pagehead"><div><h1>WhatsApp reminders</h1><p>Remind clients before their visit. Each button opens WhatsApp with the client's number and message filled in, so you only press Send.</p></div>
      <div class="seg" role="group" aria-label="Which appointments">${Object.keys(ranges).map(k => `<button type="button" id="rr-${k}" data-act="rem-range" data-r="${k}" aria-pressed="${S.remRange === k}">${labels[k]} <span class="mono muted">${unsent(k)}</span></button>`).join('')}</div></div>
    ${list.length ? `<div class="rem-sum"><span><b>${sent.length}</b> of <b>${list.length}</b> sent</span><span class="meter" aria-hidden="true"><i style="width:${Math.round(sent.length / list.length * 100)}%"></i></span>${todo.length ? `<span>${plural(todo.length, 'client')} still to remind</span>` : '<span>Everyone has been reminded.</span>'}</div>
    <div class="rem-list">${todo.length ? `<div class="rem-grp">To send · ${todo.length}</div>${todo.map(row).join('')}` : ''}${sent.length ? `<div class="rem-grp">Sent · ${sent.length}</div>${sent.map(row).join('')}` : ''}</div>`
    : `<div class="empty"><h2>Nothing to send</h2><p>No upcoming appointments ${S.remRange === 'today' ? 'later today' : S.remRange === 'tomorrow' ? 'tomorrow' : 'in the next 7 days'} need a reminder.</p></div>`}
    <section class="box"><h2>How sending works</h2>
      <ol class="steps-how"><li>Press <b>Send on WhatsApp</b>. WhatsApp opens in a new tab, or in the app on a phone, with the message ready.</li><li>Press Send in WhatsApp. Rota marks the reminder as sent when you open it, so use Undo if you didn't send it.</li><li>When a client replies to confirm, press <b>Client confirmed</b>. If an appointment moves after its reminder, it comes back to the list so you can send the new time.</li></ol>
      ${sample ? `<div class="sec"><h3>Message preview</h3><div class="copybox">${esc(reminderText(sample))}</div></div>` : ''}
      <div><button type="button" class="btn sm" data-act="nav" data-view="settings">Edit the message</button></div>
    </section>
  </div>`;
}

/* ================= settings ================= */
function timeOptions(from, to, stepMin, sel) {
  const out = []; for (let m = from; m <= to; m += stepMin) out.push(`<option value="${m}"${m === sel ? ' selected' : ''}>${hm(m)}</option>`);
  return out.join('');
}
function viewSettings() {
  if (!S.ready) return loadingHTML('Loading settings…');
  const c = cfg();
  return `<div class="page">
    <div class="pagehead"><div><h1>Settings</h1><p>Studio details appear on the booking page and in reminder texts.</p></div></div>
    <form class="form" data-form="settings" style="max-width:640px;display:flex;flex-direction:column;gap:14px">
      <div class="field"><label for="st-name">Studio name</label><input id="st-name" name="name" value="${esc(c.name)}" required></div>
      <div class="field"><label for="st-tag">Tagline</label><input id="st-tag" name="tagline" value="${esc(c.tagline)}"></div>
      <details class="tr"><summary>Armenian and Russian versions</summary><div class="tr-grid">${['hy', 'ru'].map(l => `
        <div class="row2"><div class="field"><label for="st-name-${l}">Studio name (${LANG_NAMES[l]})</label><input id="st-name-${l}" name="name_${l}" value="${esc(trRaw(c, 'name', l))}"></div>
          <div class="field"><label for="st-addr-${l}">Address (${LANG_NAMES[l]})</label><input id="st-addr-${l}" name="address_${l}" value="${esc(trRaw(c, 'address', l))}"></div></div>
        <div class="field"><label for="st-tag-${l}">Tagline (${LANG_NAMES[l]})</label><input id="st-tag-${l}" name="tagline_${l}" value="${esc(trRaw(c, 'tagline', l))}"></div>`).join('')}</div></details>
      <div class="row2"><div class="field"><label for="st-addr">Address</label><input id="st-addr" name="address" value="${esc(c.address)}"></div>
        <div class="field"><label for="st-phone">Phone</label><input id="st-phone" type="tel" name="phone" value="${esc(c.phone)}"></div></div>
      <div class="row2"><div class="field"><label for="st-open">Calendar starts</label><select id="st-open" name="open">${timeOptions(300, 780, 30, c.open)}</select></div>
        <div class="field"><label for="st-close">Calendar ends</label><select id="st-close" name="close">${timeOptions(960, 1440, 30, c.close)}</select></div></div>
      <div class="row2"><div class="field"><label for="st-step">Online booking times every</label><select id="st-step" name="step">${[15, 30, 60].map(v => `<option value="${v}"${v === c.step ? ' selected' : ''}>${v} minutes</option>`).join('')}</select></div>
        <div class="field"><label for="st-days">Clients can book ahead</label><select id="st-days" name="bookingDays">${[7, 14, 21, 30, 60].map(v => `<option value="${v}"${v === c.bookingDays ? ' selected' : ''}>${v} days</option>`).join('')}</select></div></div>
      <div class="row2"><div class="field"><label for="st-lead">Earliest online booking</label><select id="st-lead" name="leadMin">${[[0, 'Any time'], [30, '30 minutes ahead'], [60, '1 hour ahead'], [120, '2 hours ahead'], [1440, 'The next day']].map(([v, l]) => `<option value="${v}"${v === c.leadMin ? ' selected' : ''}>${l}</option>`).join('')}</select></div>
        <div class="field"><label for="st-cancel">Clients can change or cancel online</label><select id="st-cancel" name="cancelHours">${[[0, 'Until the visit starts'], [1, 'Up to 1 hour before'], [3, 'Up to 3 hours before'], [12, 'Up to 12 hours before'], [24, 'Up to 24 hours before']].map(([v, l]) => `<option value="${v}"${v === +c.cancelHours ? ' selected' : ''}>${l}</option>`).join('')}</select></div></div>
      <div class="row2"><div class="field"><label for="st-cur">Currency symbol</label><input id="st-cur" name="currency" value="${esc(c.currency)}" maxlength="4"></div>
        <div class="field"><label for="st-lang">Booking site opens in</label><select id="st-lang" name="defaultLang">${Object.entries(LANG_NAMES).map(([k, v]) => `<option value="${k}"${k === c.defaultLang ? ' selected' : ''}>${v}</option>`).join('')}</select></div></div>
      <div class="field"><label for="st-tz">Time zone</label><input id="st-tz" name="timezone" value="${esc(c.timezone)}" placeholder="Asia/Yerevan" style="max-width:240px"><small>Free times and "today" on the booking site use this.</small></div>
      <div class="field"><label for="st-cc">Country calling code</label><input id="st-cc" name="countryCode" value="${esc(c.countryCode)}" inputmode="numeric" placeholder="374" style="max-width:160px">
        <small>WhatsApp needs full international numbers. This code is added to numbers saved without one, like 091 234 567.</small></div>
      <fieldset class="field"><legend>WhatsApp reminder, in each client's language</legend>${['hy', 'ru', 'en'].map(l => `
        <label for="st-rem-${l}" class="lbl" style="margin-top:6px">${LANG_NAMES[l]}</label><textarea id="st-rem-${l}" name="reminder_${l}" rows="3">${esc((c.reminder && c.reminder[l]) || DEFAULTS.reminder[l])}</textarea>`).join('')}
        <small>Placeholders: {name}, {when}, {time}, {service}, {specialist}, {studio}, {address}, {phone}, {link} (the client's own change-or-cancel link)</small></fieldset>
      <div><button class="btn primary">Save settings</button></div>
    </form>
    <section class="box" style="max-width:640px"><h2>Your booking page</h2>
      <p style="margin:0;color:var(--ink-2)">Clients book at <b>${esc(location.origin)}/</b> in Armenian, Russian or English. Each booking gets a private link the client uses to change or cancel; reminders include it.</p>
      <div><a class="btn" href="/" target="_blank" rel="noopener">${icon('globe')}Open the booking site</a></div></section>
  </div>`;
}

/* ================= drawer ================= */
let lastFocus = null;
function openDrawer(d) {
  if (!S.drawer) lastFocus = document.activeElement;
  S.drawer = d; renderDrawer(false);
}
function closeDrawer() {
  if (!S.drawer) return;
  S.drawer = null;
  $('#drawer').classList.remove('on'); $('#scrim').classList.remove('on');
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
}
function renderDrawer(isLive) {
  const d = S.drawer; if (!d) return;
  const html = drawerHTML(d);
  if (html == null) { closeDrawer(); return; }
  const el = $('#drawer');
  if (!isLive) el.dataset.key = '';
  preserve(el, `${d.kind}:${d.id || ''}`, () => { el.innerHTML = html; });
  el.classList.add('on'); $('#scrim').classList.add('on');
  if (!isLive) {
    const f = $('[autofocus]', el) || $('.dh .iconbtn', el); if (f) f.focus({ preventScroll: true });
    if (d.kind === 'apptForm') { const form = $('form', el); apptRecalc(form, null); updateClientHint(form); }
  }
}
function drawerHTML(d) {
  switch (d.kind) {
    case 'appt': return apptDrawer(d);
    case 'apptForm': return apptFormHTML(d.seed);
    case 'client': return clientDrawer(d);
    case 'clientForm': return clientFormHTML(d.seed);
    case 'serviceForm': return serviceFormHTML(d.seed);
    case 'staffForm': return staffFormHTML(d.seed);
    default: return null;
  }
}
const dhead = (title, eyebrow = '') => `<div class="dh"><div>${eyebrow ? `<div class="eyebrow">${eyebrow}</div>` : ''}<h2>${title}</h2></div><button type="button" class="iconbtn" data-act="drawer-close" aria-label="Close">${icon('x')}</button></div>`;

const LANG_NAMES = { hy: 'Armenian', ru: 'Russian', en: 'English' };
const WHEN = {
  hy: { today: 'այսօր', tomorrow: 'վաղը', months: ['հունվարի', 'փետրվարի', 'մարտի', 'ապրիլի', 'մայիսի', 'հունիսի', 'հուլիսի', 'օգոստոսի', 'սեպտեմբերի', 'հոկտեմբերի', 'նոյեմբերի', 'դեկտեմբերի'], fmt: (d, m) => `${m} ${d}-ին` },
  ru: { today: 'сегодня', tomorrow: 'завтра', months: ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'], fmt: (d, m) => `${d} ${m}` },
  en: { today: 'today', tomorrow: 'tomorrow', months: MO, fmt: (d, m) => `on ${d} ${m}` },
};
const apptLang = a => { const l = a.lang || (clientById(a.clientId) || {}).lang || cfg().defaultLang; return WHEN[l] ? l : 'hy'; };
const trIn = (doc, field, lang) => (doc && doc.i18n && doc.i18n[lang] && doc.i18n[lang][field]) || (doc && doc[field]) || '';
function whenIn(lang, date) {
  const t = todayISO(); const W = WHEN[lang];
  if (date === t) return W.today;
  if (date === addDays(t, 1)) return W.tomorrow;
  const d = parseISO(date); return W.fmt(d.getDate(), W.months[d.getMonth()]);
}
function reminderText(a) {
  const c = cfg(); const lang = apptLang(a);
  const tpl = (c.reminder && typeof c.reminder === 'object' ? c.reminder[lang] : null) || DEFAULTS.reminder[lang];
  const st = staffById(a.staffId);
  const vals = { name: first(a.clientName), when: whenIn(lang, a.date), time: hm(a.start),
    service: (a.serviceIds || []).map(id => trIn(svcById(id), 'name', lang)).filter(Boolean).join(', '),
    specialist: first(trIn(st, 'name', lang)), studio: trIn(c, 'name', lang), address: trIn(c, 'address', lang), phone: c.phone,
    link: a.manage ? `${location.origin}/b/${a.manage}` : '' };
  return String(tpl).replace(/\{(\w+)\}/g, (m, k) => (k in vals ? vals[k] : m));
}

const awaiting = d => d.fresh && Date.now() - d.fresh < 8000 ? dhead('Saving…') + '<div class="db">' + loadingHTML('Saving…') + '</div>' : null;
function apptDrawer(d) {
  const a = S.appts.find(x => x.id === d.id); if (!a) return awaiting(d);
  const st = staffById(a.staffId); const cl = clientById(a.clientId); const cs = clientStats().get(a.clientId) || EMPTY_STATS;
  const sv = (a.serviceIds || []).map(id => svcById(id) || { id, name: 'Removed service', duration: 0, price: 0 });
  const cancelled = a.status === 'cancelled';
  const confirmBox = d.confirm === 'cancel'
    ? `<div class="confirm" role="alert">Cancel this appointment? The time becomes free for online booking.<div class="inline-row"><button type="button" class="btn danger solid sm" data-act="confirm-cancel">Cancel appointment</button><button type="button" class="btn ghost sm" data-act="confirm-no">Keep it</button></div></div>`
    : d.confirm === 'delete'
    ? `<div class="confirm" role="alert">Delete this appointment for good? It disappears from the client's history and from reports.<div class="inline-row"><button type="button" class="btn danger solid sm" data-act="confirm-delete">Delete</button><button type="button" class="btn ghost sm" data-act="confirm-no">Keep it</button></div></div>` : '';
  const rem = reminderText(a);
  return dhead(`${hm(a.start)}–${hm(a.start + a.duration)}`, esc(`${relDay(a.date) ? relDay(a.date) + ' · ' : ''}${fmtDay(a.date)}`)) + `<div class="db" data-keep="db">
    <div class="who-row"><span class="av ${colorCls(st)}">${esc(initials(st ? st.name : '?'))}</span><div class="who"><b>${esc(st ? st.name : 'Unassigned')}</b><small>${esc(st ? st.role || '' : '')}</small></div><span class="pill s-${esc(a.status)}">${STATUS[a.status] || a.status}</span></div>
    ${confirmBox}
    <section class="sec"><h3>Client</h3>
      <div class="who-row"><span class="av">${esc(initials(a.clientName))}</span><div class="who">${cl ? `<button type="button" class="linkish" data-act="open-client" data-id="${esc(cl.id)}"><b>${esc(a.clientName)}</b></button>` : `<b>${esc(a.clientName || 'Walk-in')}</b>`}<small>${cs.visits ? `${plural(cs.visits, 'visit')}${cs.last ? ` · last ${esc(fmtShort(cs.last))}` : ''} · ${money(cs.spent)} spent` : 'First visit'}${cs.noshow ? ` · ${plural(cs.noshow, 'no-show')}` : ''}</small></div></div>
      ${a.clientPhone ? `<div class="inline-row"><span class="mono" style="user-select:text">${esc(a.clientPhone)}</span><button type="button" class="btn ghost sm" data-act="copy" data-text="${esc(a.clientPhone)}" data-label="Phone number">${icon('copy')}Copy</button></div>` : ''}
      ${cl && cl.notes ? `<div class="note">${esc(cl.notes)}</div>` : ''}
      ${a.note ? `<div class="copybox"><b>Booking note:</b> ${esc(a.note)}</div>` : ''}
    </section>
    <section class="sec"><h3>Services</h3><div class="lines">${sv.map(s => `<div class="line"><span class="grow">${esc(s.name)}</span><small class="mono">${s.duration ? durTxt(s.duration) : ''}</small><span class="mono">${money(s.price)}</span></div>`).join('')}
      <div class="line total"><span class="grow">Total · ${durTxt(a.duration)}</span><span class="mono">${money(a.price)}</span></div></div></section>
    ${cancelled ? `<section class="sec"><h3>Status</h3><div class="inline-row"><span class="muted">Cancelled. The time is free again.</span><button type="button" class="btn sm" data-act="restore-appt">Restore</button></div></section>` : `
    <section class="sec"><h3>Status</h3><div class="seg" role="group" aria-label="Status">${['booked', 'confirmed', 'arrived', 'noshow'].map(s => `<button type="button" id="ast-${s}" data-act="set-status" data-status="${s}" aria-pressed="${a.status === s}">${STATUS[s]}</button>`).join('')}</div></section>
    <section class="sec"><h3>Payment</h3>${a.paid > 0
      ? `<div class="paid-row"><span>Paid ${money(a.paid)}${a.payMethod ? ` · ${esc(a.payMethod)}` : ''}</span><button type="button" class="btn ghost sm" data-act="undo-pay">Undo</button></div>`
      : `<form class="pay" data-form="pay"><div class="seg" role="radiogroup" aria-label="Method"><label><input type="radio" name="method" value="card" checked>Card</label><label><input type="radio" name="method" value="cash">Cash</label></div><label class="sr" for="pay-amt">Amount</label><input id="pay-amt" name="amount" inputmode="numeric" value="${esc(a.price)}" data-sticky><button class="btn primary sm">Take payment</button></form>`}</section>
    ${remindable(a) || a.remindedAt ? `<section class="sec"><h3>WhatsApp reminder</h3>${waStatusLine(a)}<div class="copybox" id="rem-text">${esc(rem)}</div><div class="inline-row">${waButton(a)}<button type="button" class="btn sm" data-act="copy" data-text="${esc(rem)}" data-label="Reminder">${icon('copy')}Copy text</button>${remState(a) !== 'todo' ? '<button type="button" class="btn ghost sm" data-act="wa-unsend" data-id="' + esc(a.id) + '">Mark as not sent</button>' : ''}</div></section>` : ''}`}
    ${a.cancelledBy === 'client' ? '<div class="note">The client cancelled this visit online.</div>' : a.movedFrom && a.clientAction === 'rescheduled' ? `<div class="note">The client moved this visit online from ${esc(fmtDay(a.movedFrom.split(' ')[0]))} at ${hm(+a.movedFrom.split(' ')[1])}.</div>` : ''}
    <p class="muted" style="margin:0;font-size:12.5px">${esc(SOURCE[a.source] || 'Booked')}${a.lang ? ` · ${LANG_NAMES[a.lang] || a.lang}` : ''}${a.createdAt ? ` · ${esc(fmtShort(iso(new Date(a.createdAt))))}, ${hm(new Date(a.createdAt).getHours() * 60 + new Date(a.createdAt).getMinutes())}` : ''}</p>
  </div>
  <div class="df"><button type="button" class="btn sm" data-act="edit-appt">Edit</button><button type="button" class="btn sm" data-act="book-again">Book again</button><span class="grow"></span>${cancelled ? '' : '<button type="button" class="btn sm danger" data-act="cancel-appt">Cancel</button>'}<button type="button" class="btn sm ghost danger" data-act="delete-appt">Delete</button></div>`;
}

function apptFormHTML(seed) {
  const c = cfg(); const isEdit = !!seed.id;
  const staff = sortedStaff();
  const staffId = seed.staffId || (staff.find(s => hoursOn(s, seed.date || S.date)) || staff[0] || {}).id || '';
  const date = seed.date || S.date;
  const start = seed.start ?? defaultStart(date);
  const ids = new Set(seed.serviceIds || []);
  const cl = seed.clientId ? clientById(seed.clientId) : null;
  const name = seed.clientName ?? (cl ? cl.name : '');
  const phone = seed.clientPhone ?? (cl ? cl.phone : '');
  const dur = seed.duration || sum([...ids].map(svcById).filter(Boolean), s => s.duration) || 60;
  const price = seed.price ?? sum([...ids].map(svcById).filter(Boolean), s => s.price);
  const lo = Math.max(0, Math.min(c.open - 60, start)); const hi = Math.min(1425, Math.max(c.close + 60, start));
  const durOpts = []; for (let m = 15; m <= 480; m += 15) durOpts.push(m);
  if (!durOpts.includes(dur)) durOpts.push(dur);
  const cats = groupBy(sortedServices(), s => s.category || 'Other');
  return dhead(isEdit ? 'Edit appointment' : 'New appointment', isEdit ? esc(fmtDay(seed.date)) : '') + `<form class="dform" data-form="appt" autocomplete="off" novalidate>
    <div class="db">
      <input type="hidden" name="apptId" value="${esc(seed.id || '')}"><input type="hidden" name="clientId" value="${esc(seed.clientId || '')}">
      <div class="field"><label for="f-client">Client</label><div class="combo"><input id="f-client" name="clientName" value="${esc(name)}" placeholder="Search by name or phone, or type a new name"${name ? '' : ' autofocus'}><div class="combo-list" id="f-client-list" hidden></div></div><span class="hint" id="f-client-hint"></span></div>
      <div class="field"><label for="f-phone">Phone</label><input id="f-phone" type="tel" name="phone" value="${esc(phone)}" placeholder="+374 …"></div>
      <div class="row2"><div class="field"><label for="f-staff">Specialist</label><select id="f-staff" name="staffId">${staff.map(s => `<option value="${esc(s.id)}"${s.id === staffId ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}</select></div>
        <div class="field"><label for="f-date">Date</label><input id="f-date" type="date" name="date" value="${esc(date)}" required></div></div>
      <div class="row2"><div class="field"><label for="f-start">Start</label><select id="f-start" name="start">${timeOptions(lo - lo % 15, hi, 15, start)}</select></div>
        <div class="field"><label for="f-dur">Duration <span class="muted" id="f-end"></span></label><select id="f-dur" name="duration">${durOpts.sort((x, y) => x - y).map(m => `<option value="${m}"${m === dur ? ' selected' : ''}>${durTxt(m)}</option>`).join('')}</select></div></div>
      <fieldset class="field"><legend>Services</legend><div class="checks">${[...cats].map(([cat, items]) => `<div class="cat-h">${esc(cat)}</div>` + items.map(s => `<label class="check" data-svc="${esc(s.id)}"><input type="checkbox" name="svc" value="${esc(s.id)}"${ids.has(s.id) ? ' checked' : ''}><span class="grow">${esc(s.name)}</span><small>${durTxt(s.duration)} · ${money(s.price)}</small></label>`).join('')).join('') || '<p class="muted" style="padding:10px 12px;margin:0">Add services first.</p>'}</div></fieldset>
      <div class="row2"><div class="field"><label for="f-price">Price</label><input id="f-price" name="price" inputmode="numeric" value="${esc(price)}"></div>
        <div class="field"><label for="f-source">Booked via</label><select id="f-source" name="source">${Object.entries(SOURCE).map(([k, v]) => `<option value="${k}"${(seed.source || 'desk') === k ? ' selected' : ''}>${v}</option>`).join('')}</select></div></div>
      <div class="field"><label for="f-note">Note</label><textarea id="f-note" name="note" rows="2" placeholder="Visible to the team">${esc(seed.note || '')}</textarea></div>
      <div class="note" id="f-warn" hidden></div>
      <p class="form-err" id="f-err" role="alert"></p>
    </div>
    <div class="df"><span class="grow"></span><button type="button" class="btn" data-act="drawer-close">Cancel</button><button class="btn primary">${isEdit ? 'Save changes' : 'Book appointment'}</button></div>
  </form>`;
}

function apptRecalc(form, changed) {
  if (!form) return;
  const F = n => form.elements.namedItem(n);
  const ids = $$('input[name="svc"]:checked', form).map(i => i.value);
  const sv = ids.map(svcById).filter(Boolean);
  if (changed && changed.name === 'duration') form.dataset.manualDur = '1';
  if (changed && changed.name === 'price') form.dataset.manualPrice = '1';
  if (changed && changed.name === 'svc') {
    const d = sum(sv, s => s.duration), p = sum(sv, s => s.price);
    if (!form.dataset.manualDur && d) { const sel = F('duration'); if (![...sel.options].some(o => +o.value === d)) sel.add(new Option(durTxt(d), d)); sel.value = String(d); }
    if (!form.dataset.manualPrice) F('price').value = p ? String(p) : '';
  }
  const st = staffById(F('staffId').value);
  $$('.check[data-svc]', form).forEach(l => l.classList.toggle('dim', !!st && !(st.services || []).includes(l.dataset.svc)));
  const date = F('date').value, start = +F('start').value, dur = +F('duration').value, id = F('apptId').value;
  $('#f-end', form).textContent = `· ends ${hm(start + dur)}`;
  const msgs = [];
  if (st && date) {
    S.appts.filter(a => a.id !== id && a.staffId === st.id && a.date === date && a.status !== 'cancelled' && start < a.start + a.duration && start + dur > a.start)
      .forEach(a => msgs.push(`Overlaps ${a.clientName || 'another'}'s ${hm(a.start)}–${hm(a.start + a.duration)} appointment.`));
    const h = hoursOn(st, date);
    if (!h) msgs.push(`${first(st.name)} doesn't work on ${WDL[dow(date)]}s.`);
    else if (start < h[0] || start + dur > h[1]) msgs.push(`Outside ${first(st.name)}'s hours that day (${hm(h[0])}–${hm(h[1])}).`);
    const notOffered = sv.filter(s => !(st.services || []).includes(s.id));
    if (notOffered.length) msgs.push(`${first(st.name)} doesn't usually do ${notOffered.map(s => s.name).join(', ')}.`);
  }
  const w = $('#f-warn', form); w.hidden = !msgs.length; w.innerHTML = msgs.map(m => `<span>${esc(m)}</span>`).join('');
}
function clientCombo(form, q) {
  form.elements.namedItem('clientId').value = '';
  const list = $('#f-client-list', form);
  const s = q.trim().toLowerCase(); const qd = digits(q);
  const hits = !s ? [] : S.clients.filter(c => String(c.name).toLowerCase().includes(s) || (qd.length >= 3 && digits(c.phone).includes(qd))).slice(0, 6);
  list.innerHTML = hits.map(c => `<button type="button" data-act="pick-client" data-id="${esc(c.id)}"><span class="av sm">${esc(initials(c.name))}</span><span><b>${esc(c.name)}</b><small>${esc(c.phone || '')}</small></span></button>`).join('');
  list.hidden = !hits.length;
  updateClientHint(form);
}
function updateClientHint(form) {
  const hint = $('#f-client-hint', form); if (!hint) return;
  const F = n => form.elements.namedItem(n);
  const id = F('clientId').value;
  if (id) { const cs = clientStats().get(id) || EMPTY_STATS; hint.textContent = `Returning client · ${plural(cs.visits, 'visit')}${cs.noshow ? ` · ${plural(cs.noshow, 'no-show')}` : ''}`; return; }
  const ph = F('phone').value; const m = ph && S.clients.find(c => samePhone(c.phone, ph));
  hint.textContent = m ? `Same phone as ${m.name}, so this goes on their profile.` : (F('clientName').value.trim() ? 'New client. A profile is created when you save.' : '');
}

async function submitAppt(form) {
  const F = n => form.elements.namedItem(n);
  const err = $('#f-err', form);
  const ids = $$('input[name="svc"]:checked', form).map(i => i.value);
  const name = F('clientName').value.trim(); const phone = F('phone').value.trim();
  F('clientName').removeAttribute('aria-invalid');
  if (!name) { err.textContent = 'Add the client’s name.'; F('clientName').setAttribute('aria-invalid', 'true'); F('clientName').focus(); return; }
  if (!ids.length) { err.textContent = 'Pick at least one service.'; const box = $('input[name="svc"]', form); if (box) box.focus(); return; }
  if (!F('date').value) { err.textContent = 'Pick a date.'; return; }
  err.textContent = '';
  const existing = S.appts.find(a => a.id === F('apptId').value) || null;
  let clientId = F('clientId').value;
  if (!clientId && phone) clientId = (S.clients.find(c => samePhone(c.phone, phone)) || {}).id || '';
  if (!clientId) {
    clientId = uid('c_');
    if (!await save('clients', clientId, { name, phone, email: '', notes: '', createdAt: nowISO(), source: F('source').value })) return;
  } else {
    const cl = clientById(clientId);
    if (cl && phone && !cl.phone) save('clients', clientId, { ...cl, phone });
  }
  const a = {
    ...(existing || { status: 'booked', paid: 0, payMethod: null, createdAt: nowISO() }),
    date: F('date').value, start: +F('start').value, duration: +F('duration').value, staffId: F('staffId').value,
    serviceIds: ids, clientId, clientName: name, clientPhone: phone, price: +digits(F('price').value) || 0,
    source: F('source').value, note: F('note').value.trim(),
  };
  const id = existing ? existing.id : uid('a_');
  if (await save('appointments', id, a, existing ? 'Appointment updated' : `Booked ${first(name)} for ${dayName(a.date)} at ${hm(a.start)}`)) {
    S.date = a.date; if (S.view !== 'calendar' && S.view !== 'clients') S.view = 'calendar';
    S.drawer = { kind: 'appt', id, live: true, fresh: Date.now() }; render(); renderDrawer(false);
  }
}

function clientDrawer(d) {
  const cl = clientById(d.id); if (!cl) return awaiting(d);
  const cs = clientStats().get(cl.id) || EMPTY_STATS;
  const hist = S.appts.filter(a => a.clientId === cl.id).sort((a, b) => (b.date + hm(b.start)).localeCompare(a.date + hm(a.start)));
  const up = hist.filter(isUpcoming).reverse(); const past = hist.filter(a => !isUpcoming(a));
  const row = a => { const st = staffById(a.staffId); return `<button type="button" class="line" data-act="open-appt" data-id="${esc(a.id)}"><span class="av sm ${colorCls(st)}">${esc(initials(st ? st.name : '?'))}</span><span class="grow"><b>${esc(fmtDay(a.date))}</b> <span class="mono">${hm(a.start)}</span><br><small>${esc(svcNames(a))}</small></span><span class="pill s-${esc(a.status)}">${a.paid > 0 && a.status === 'arrived' ? money(a.paid) : STATUS[a.status]}</span></button>`; };
  const confirmBox = d.confirm === 'delete' ? `<div class="confirm" role="alert">Delete ${esc(cl.name)}'s profile? Their past appointments stay in the calendar under their name.<div class="inline-row"><button type="button" class="btn danger solid sm" data-act="confirm-delete-client">Delete profile</button><button type="button" class="btn ghost sm" data-act="confirm-no">Keep it</button></div></div>` : '';
  return dhead(esc(cl.name), 'Client') + `<div class="db" data-keep="db">
    ${confirmBox}
    <dl class="mini-tiles"><div><dt>Visits</dt><dd>${cs.visits}</dd></div><div><dt>Spent</dt><dd>${shortMoney(cs.spent)}</dd></div><div><dt>Avg ticket</dt><dd>${cs.visits ? shortMoney(cs.spent / cs.visits) : '—'}</dd></div><div><dt>No-shows</dt><dd>${cs.noshow}</dd></div></dl>
    <dl class="kv"><dt>Phone</dt><dd><span class="mono" style="user-select:text">${esc(cl.phone || '—')}</span>${cl.phone ? ` <button type="button" class="btn ghost sm" data-act="copy" data-text="${esc(cl.phone)}" data-label="Phone number">${icon('copy')}Copy</button>` : ''}</dd>
      ${cl.email ? `<dt>Email</dt><dd style="user-select:text">${esc(cl.email)}</dd>` : ''}
      <dt>Client since</dt><dd>${cl.createdAt ? esc(fmtShort(iso(new Date(cl.createdAt)))) + ' ' + new Date(cl.createdAt).getFullYear() : '—'}${cl.source === 'online' ? ' · first booked online' : ''}</dd></dl>
    <form class="sec" data-form="notes"><h3><label for="cl-notes">Notes</label></h3><textarea id="cl-notes" name="notes" rows="3" placeholder="Preferences, allergies, formulas…" data-sticky>${esc(cl.notes || '')}</textarea><div><button class="btn sm">Save notes</button></div></form>
    <section class="sec"><h3>Upcoming</h3>${up.length ? `<div class="lines">${up.map(row).join('')}</div>` : '<p class="muted" style="margin:0">Nothing booked.</p>'}</section>
    <section class="sec"><h3>History</h3>${past.length ? `<div class="lines">${past.map(row).join('')}</div>` : '<p class="muted" style="margin:0">No past visits yet.</p>'}</section>
  </div>
  <div class="df"><button type="button" class="btn primary sm" data-act="appt-for-client">${icon('plus')}New appointment</button><button type="button" class="btn sm" data-act="edit-client">Edit</button><span class="grow"></span><button type="button" class="btn sm ghost danger" data-act="delete-client">Delete</button></div>`;
}

function clientFormHTML(seed) {
  return dhead(seed.id ? 'Edit client' : 'New client') + `<form class="dform" data-form="client" novalidate><div class="db">
    <input type="hidden" name="cid" value="${esc(seed.id || '')}">
    <div class="field"><label for="c-name">Name</label><input id="c-name" name="name" value="${esc(seed.name || '')}" autofocus required></div>
    <div class="field"><label for="c-phone">Phone</label><input id="c-phone" type="tel" name="phone" value="${esc(seed.phone || '')}" placeholder="+374 …"></div>
    <div class="field"><label for="c-email">Email</label><input id="c-email" type="email" name="email" value="${esc(seed.email || '')}"></div>
    <div class="field"><label for="c-notes">Notes</label><textarea id="c-notes" name="notes" rows="3">${esc(seed.notes || '')}</textarea></div>
    <p class="form-err" id="c-err" role="alert"></p>
  </div><div class="df"><span class="grow"></span><button type="button" class="btn" data-act="drawer-close">Cancel</button><button class="btn primary">Save client</button></div></form>`;
}

function serviceFormHTML(seed) {
  const cats = [...new Set(S.services.map(s => s.category).filter(Boolean))];
  const durOpts = []; for (let m = 15; m <= 300; m += 15) durOpts.push(m);
  const dur = seed.duration || 60;
  const confirmBox = seed._confirm ? `<div class="confirm" role="alert">Delete this service? Past appointments keep their record but show it as a removed service.<div class="inline-row"><button type="button" class="btn danger solid sm" data-act="confirm-delete-service">Delete service</button><button type="button" class="btn ghost sm" data-act="confirm-no-form">Keep it</button></div></div>` : '';
  return dhead(seed.id ? 'Edit service' : 'New service') + `<form class="dform" data-form="service" novalidate><div class="db">
    ${confirmBox}
    <input type="hidden" name="sid" value="${esc(seed.id || '')}">
    <div class="field"><label for="s-name">Name</label><input id="s-name" name="name" value="${esc(seed.name || '')}" autofocus required></div>
    <div class="field"><label for="s-desc">Description for clients</label><textarea id="s-desc" name="description" rows="2" placeholder="What's included, in a sentence">${esc(seed.description || '')}</textarea></div>
    ${trFields(seed, ['name', 'description', 'category'], 's-tr')}
    <div class="field"><label for="s-cat">Category</label><input id="s-cat" name="category" list="s-cats" value="${esc(seed.category || '')}" placeholder="Hair, Nails…"><datalist id="s-cats">${cats.map(c => `<option value="${esc(c)}">`).join('')}</datalist></div>
    <div class="row2"><div class="field"><label for="s-dur">Duration</label><select id="s-dur" name="duration">${(durOpts.includes(dur) ? durOpts : [...durOpts, dur]).map(m => `<option value="${m}"${m === dur ? ' selected' : ''}>${durTxt(m)}</option>`).join('')}</select></div>
      <div class="field"><label for="s-price">Price (${esc(cfg().currency)})</label><input id="s-price" name="price" inputmode="numeric" value="${esc(seed.price ?? '')}" required></div></div>
    <label class="check" style="border:1px solid var(--rule);border-radius:10px"><input type="checkbox" name="online"${seed.online === false ? '' : ' checked'}><span class="grow">Clients can book it online</span></label>
    <fieldset class="field"><legend>Who performs it</legend><div class="checks">${sortedStaff().map(st => `<label class="check"><input type="checkbox" name="who" value="${esc(st.id)}"${seed.id && (st.services || []).includes(seed.id) ? ' checked' : ''}><span class="av sm ${colorCls(st)}">${esc(initials(st.name))}</span><span class="grow">${esc(st.name)}</span></label>`).join('') || '<p class="muted" style="padding:10px 12px;margin:0">Add specialists in Team first.</p>'}</div></fieldset>
    <p class="form-err" id="s-err" role="alert"></p>
  </div><div class="df">${seed.id ? '<button type="button" class="btn ghost danger" data-act="delete-service">Delete</button>' : ''}<span class="grow"></span><button type="button" class="btn" data-act="drawer-close">Cancel</button><button class="btn primary">Save service</button></div></form>`;
}

function staffFormHTML(seed) {
  const hours = seed.hours || { 1: [600, 1140], 2: [600, 1140], 3: [600, 1140], 4: [600, 1140], 5: [600, 1140] };
  const color = seed.color ?? (S.staff.length % 6);
  const opts = (sel) => timeOptions(360, 1410, 30, sel);
  const confirmBox = seed._confirm ? `<div class="confirm" role="alert">${seed._confirm === 'blocked' ? `${esc(first(seed.name))} has upcoming appointments. Move or cancel them first.<div class="inline-row"><button type="button" class="btn ghost sm" data-act="confirm-no-form">OK</button></div>` : `Remove ${esc(seed.name)} from the team? Their past appointments stay in reports.<div class="inline-row"><button type="button" class="btn danger solid sm" data-act="confirm-delete-staff">Remove</button><button type="button" class="btn ghost sm" data-act="confirm-no-form">Keep</button></div>`}</div>` : '';
  return dhead(seed.id ? 'Edit specialist' : 'New specialist') + `<form class="dform" data-form="staff" novalidate><div class="db">
    ${confirmBox}
    <input type="hidden" name="tid" value="${esc(seed.id || '')}">
    <div class="row2"><div class="field"><label for="t-name">Name</label><input id="t-name" name="name" value="${esc(seed.name || '')}" autofocus required></div>
      <div class="field"><label for="t-role">Role</label><input id="t-role" name="role" value="${esc(seed.role || '')}" placeholder="Stylist, Barber…"></div></div>
    <div class="field"><label for="t-bio">Short bio for clients</label><textarea id="t-bio" name="bio" rows="2" placeholder="What they're known for">${esc(seed.bio || '')}</textarea></div>
    ${trFields(seed, ['name', 'role', 'bio'], 't-tr')}
    <fieldset class="field"><legend>Calendar color</legend><div class="swatches">${[0, 1, 2, 3, 4, 5].map(i => `<label class="c${i}"><input type="radio" name="color" value="${i}"${i === color ? ' checked' : ''}><span class="av c${i}">${esc(initials(seed.name || 'A B'))}</span><span class="sr">Color ${i + 1}</span></label>`).join('')}</div></fieldset>
    <fieldset class="field"><legend>Working hours</legend><div class="hours">${WEEK.map(w => { const h = hours[w]; return `<div class="hrow"><label><input type="checkbox" name="w${w}" data-day="${w}"${h ? ' checked' : ''}>${WD[w]}</label><select name="s${w}" aria-label="${WDL[w]} start"${h ? '' : ' disabled'}>${opts(h ? h[0] : 600)}</select><select name="e${w}" aria-label="${WDL[w]} end"${h ? '' : ' disabled'}>${opts(h ? h[1] : 1140)}</select></div>`; }).join('')}</div></fieldset>
    <fieldset class="field"><legend>Services</legend><div class="checks">${sortedServices().map(s => `<label class="check"><input type="checkbox" name="svc" value="${esc(s.id)}"${(seed.services || []).includes(s.id) ? ' checked' : ''}><span class="grow">${esc(s.name)}</span><small>${esc(s.category || '')}</small></label>`).join('') || '<p class="muted" style="padding:10px 12px;margin:0">Add services first.</p>'}</div></fieldset>
    <p class="form-err" id="t-err" role="alert"></p>
  </div><div class="df">${seed.id ? '<button type="button" class="btn ghost danger" data-act="delete-staff">Remove</button>' : ''}<span class="grow"></span><button type="button" class="btn" data-act="drawer-close">Cancel</button><button class="btn primary">Save</button></div></form>`;
}

/* ================= toasts ================= */
function toast(msg, action) {
  const box = $('#toasts'); const el = document.createElement('div'); el.className = 'toast';
  const span = document.createElement('span'); span.textContent = msg; el.appendChild(span);
  if (action) { const b = document.createElement('button'); b.type = 'button'; b.textContent = action.label; b.addEventListener('click', () => { el.remove(); action.fn(); }); el.appendChild(b); }
  box.appendChild(el);
  while (box.children.length > 3) box.firstChild.remove();
  setTimeout(() => el.remove(), action ? 7000 : 3800);
}
function copyText(text, label) {
  try {
    navigator.clipboard.writeText(text).then(() => toast(`${label} copied`), () => toast('Copying is blocked here. Select the text and copy it yourself.'));
  } catch (e) { toast('Copying is blocked here. Select the text and copy it yourself.'); }
}

/* ================= actions ================= */
function go(view) {
  S.view = view; closeDrawer();
  try { history.replaceState(null, '', '#' + view); } catch (e) { /* sandboxed */ }
  render(); $('#main').scrollTop = 0;
}
const curAppt = () => S.drawer && S.appts.find(a => a.id === S.drawer.id);
function setAppt(patch, msg) { const a = curAppt(); if (!a) return; return save('appointments', a.id, { ...a, ...patch }, msg); }

const ACT = {
  'nav': el => go(el.dataset.view),
  'day': el => { S.date = addDays(S.date, +el.dataset.n); render(); },
  'today': () => { S.date = todayISO(); S.scrolledFor = null; render(); },
  'goto': el => { S.date = el.dataset.date; render(); },
  'new-appt': () => openDrawer({ kind: 'apptForm', seed: { date: S.date } }),
  'open-appt': el => openDrawer({ kind: 'appt', id: el.dataset.id, live: true }),
  'edit-appt': () => { const a = curAppt(); if (a) openDrawer({ kind: 'apptForm', seed: { ...a } }); },
  'book-again': () => {
    const a = curAppt(); if (!a) return; const t = todayISO();
    openDrawer({ kind: 'apptForm', seed: { clientId: a.clientId, clientName: a.clientName, clientPhone: a.clientPhone, serviceIds: a.serviceIds, staffId: a.staffId, duration: a.duration, price: a.price, start: a.start, date: a.date >= t ? addDays(a.date, 28) : t } });
  },
  'set-status': el => { const s = el.dataset.status; const a = curAppt(); if (a && a.status !== s) setAppt({ status: s }, `Marked ${STATUS[s].toLowerCase()}`); },
  'cancel-appt': () => { S.drawer.confirm = 'cancel'; renderDrawer(true); },
  'delete-appt': () => { S.drawer.confirm = 'delete'; renderDrawer(true); },
  'confirm-no': () => { S.drawer.confirm = null; renderDrawer(true); },
  'confirm-cancel': () => { S.drawer.confirm = null; setAppt({ status: 'cancelled', cancelledAt: nowISO(), cancelledBy: 'studio' }, 'Appointment cancelled'); },
  'restore-appt': () => setAppt({ status: 'booked', cancelledAt: null, cancelledBy: null }, 'Appointment restored'),
  'confirm-delete': async () => { const a = curAppt(); if (!a) return; closeDrawer(); await remove('appointments', a.id, 'Appointment deleted'); },
  'undo-pay': () => setAppt({ paid: 0, payMethod: null, paidAt: null }, 'Payment removed'),
  'copy': el => copyText(el.dataset.text, el.dataset.label || 'Text'),
  'wa-send': el => {
    const a = S.appts.find(x => x.id === el.dataset.id); if (!a) return;
    const prev = { ...a };
    save('appointments', a.id, { ...a, remindedAt: nowISO(), remindedFor: remKey(a), remindedVia: 'whatsapp' })
      .then(ok => { if (ok) toast(`Reminder to ${first(a.clientName)} marked as sent`, { label: 'Undo', fn: () => save('appointments', prev.id, prev) }); });
  },
  'wa-unsend': el => { const a = S.appts.find(x => x.id === el.dataset.id); if (a) save('appointments', a.id, { ...a, remindedAt: null, remindedFor: null, remindedVia: null }, 'Marked as not sent'); },
  'rem-confirm': el => { const a = S.appts.find(x => x.id === el.dataset.id); if (a) save('appointments', a.id, { ...a, status: 'confirmed' }, `${first(a.clientName)} is confirmed`); },
  'rem-range': el => { S.remRange = el.dataset.r; render(); },
  'edit-appt-id': el => { const a = S.appts.find(x => x.id === el.dataset.id); if (a) openDrawer({ kind: 'apptForm', seed: { ...a } }); },
  'drawer-close': () => closeDrawer(),
  'pick-client': el => {
    const form = el.closest('form'); const cl = clientById(el.dataset.id); if (!form || !cl) return;
    const F = n => form.elements.namedItem(n);
    F('clientId').value = cl.id; F('clientName').value = cl.name; if (cl.phone) F('phone').value = cl.phone;
    $('#f-client-list', form).hidden = true; updateClientHint(form); F('phone').focus();
  },
  'open-client': el => openDrawer({ kind: 'client', id: el.dataset.id, live: true }),
  'new-client': () => openDrawer({ kind: 'clientForm', seed: {} }),
  'edit-client': () => { const cl = clientById(S.drawer.id); if (cl) openDrawer({ kind: 'clientForm', seed: { ...cl } }); },
  'delete-client': () => { S.drawer.confirm = 'delete'; renderDrawer(true); },
  'confirm-delete-client': async () => { const id = S.drawer.id; closeDrawer(); await remove('clients', id, 'Client profile deleted'); },
  'appt-for-client': () => { const cl = clientById(S.drawer.id); if (cl) openDrawer({ kind: 'apptForm', seed: { clientId: cl.id, clientName: cl.name, clientPhone: cl.phone, date: S.date >= todayISO() ? S.date : todayISO() } }); },
  'client-filter': el => { S.cf = el.dataset.f; render(); },
  'client-sort': el => { S.cs = el.dataset.k; render(); },
  'new-service': () => openDrawer({ kind: 'serviceForm', seed: {} }),
  'edit-service': el => { const s = svcById(el.dataset.id); if (s) openDrawer({ kind: 'serviceForm', seed: { ...s } }); },
  'delete-service': () => { S.drawer.seed._confirm = true; renderDrawer(false); },
  'confirm-no-form': () => { S.drawer.seed._confirm = null; renderDrawer(false); },
  'confirm-delete-service': async () => {
    const id = S.drawer.seed.id; closeDrawer();
    for (const st of S.staff.filter(x => (x.services || []).includes(id))) await save('staff', st.id, { ...st, services: st.services.filter(x => x !== id) });
    await remove('services', id, 'Service deleted');
  },
  'new-staff': () => { if (S.view !== 'team') go('team'); openDrawer({ kind: 'staffForm', seed: {} }); },
  'edit-staff': el => { const s = staffById(el.dataset.id); if (s) openDrawer({ kind: 'staffForm', seed: { ...s } }); },
  'delete-staff': () => { const seed = S.drawer.seed; seed._confirm = S.appts.some(a => a.staffId === seed.id && isUpcoming(a)) ? 'blocked' : 'ask'; renderDrawer(false); },
  'confirm-delete-staff': async () => { const id = S.drawer.seed.id; closeDrawer(); await remove('staff', id, 'Specialist removed'); },
  'report-range': el => { S.range = +el.dataset.n; render(); },
  logout: async () => { try { await api('/api/auth/logout', { method: 'POST', body: {}, quiet: true }); } catch (e) { /* signed out anyway */ } signedOut(); },
};

const FORMS = {
  appt: submitAppt,
  login: async form => {
    const F = n => form.elements.namedItem(n);
    const email = F('email').value.trim(), password = F('password').value;
    S.login.email = email;
    if (!email || !password) { S.login.error = 'Enter your email and password.'; render(); $(email ? '#li-pw' : '#li-email')?.focus(); return; }
    try {
      S.user = await api('/api/auth/login', { method: 'POST', body: { email, password }, quiet: true });
      S.login.error = ''; S.mode = 'connecting'; render(); startSync();
    } catch (e) {
      S.login.error = e.status === 429 ? 'Too many tries. Wait a few minutes and try again.' : e.status === 401 ? "That email and password don't match." : "Can't reach the server. Check your connection.";
      render(); const pw = $('#li-pw'); if (pw) { pw.value = ''; pw.focus(); }
    }
  },
  pay: async form => {
    const a = curAppt(); if (!a) return;
    const amount = +digits(form.elements.namedItem('amount').value) || 0;
    if (!amount) { toast('Enter the amount you took.'); return; }
    const method = (form.querySelector('input[name="method"]:checked') || {}).value || 'card';
    await setAppt({ paid: amount, payMethod: method, paidAt: nowISO(), status: a.status === 'noshow' ? a.status : 'arrived' }, `Payment of ${money(amount)} recorded`);
  },
  notes: async form => { const cl = clientById(S.drawer.id); if (!cl) return; await save('clients', cl.id, { ...cl, notes: form.elements.namedItem('notes').value.trim() }, 'Notes saved'); },
  client: async form => {
    const F = n => form.elements.namedItem(n);
    const name = F('name').value.trim(); if (!name) { $('#c-err', form).textContent = 'Add a name.'; return; }
    const id = F('cid').value || uid('c_'); const prev = clientById(id) || { createdAt: nowISO(), source: 'desk' };
    if (await save('clients', id, { ...prev, name, phone: F('phone').value.trim(), email: F('email').value.trim(), notes: F('notes').value.trim() }, prev.name ? 'Client updated' : 'Client added')) {
      S.drawer = { kind: 'client', id, live: true, fresh: Date.now() }; renderDrawer(false);
    }
  },
  service: async form => {
    const F = n => form.elements.namedItem(n);
    const name = F('name').value.trim(); const price = +digits(F('price').value);
    if (!name) { $('#s-err', form).textContent = 'Add a name.'; return; }
    if (!F('price').value.trim()) { $('#s-err', form).textContent = 'Add a price. Use 0 for a free service.'; return; }
    const id = F('sid').value || uid('sv_'); const prev = svcById(id);
    const data = { ...(prev || { order: (Math.max(0, ...S.services.map(s => s.order || 0)) + 1) }), name, category: F('category').value.trim() || 'Other', description: F('description').value.trim(), i18n: readTr(form, ['name', 'description', 'category']), duration: +F('duration').value, price: price || 0, online: F('online').checked };
    if (!await save('services', id, data)) return;
    const who = new Set($$('input[name="who"]:checked', form).map(i => i.value));
    for (const st of S.staff) {
      const has = (st.services || []).includes(id);
      if (who.has(st.id) && !has) await save('staff', st.id, { ...st, services: [...(st.services || []), id] });
      if (!who.has(st.id) && has) await save('staff', st.id, { ...st, services: st.services.filter(x => x !== id) });
    }
    toast(prev ? 'Service updated' : 'Service added'); closeDrawer();
  },
  staff: async form => {
    const F = n => form.elements.namedItem(n);
    const name = F('name').value.trim(); if (!name) { $('#t-err', form).textContent = 'Add a name.'; return; }
    const hours = {};
    for (const w of [0, 1, 2, 3, 4, 5, 6]) {
      if (F('w' + w).checked) {
        const s = +F('s' + w).value, e = +F('e' + w).value;
        if (e <= s) { $('#t-err', form).textContent = `${WDL[w]}: the end time must be after the start time.`; return; }
        hours[w] = [s, e];
      } else hours[w] = null;
    }
    const id = F('tid').value || uid('st_'); const prev = staffById(id);
    const data = { ...(prev || { order: S.staff.length + 1 }), name, role: F('role').value.trim(), bio: F('bio').value.trim(), i18n: readTr(form, ['name', 'role', 'bio']), color: +((form.querySelector('input[name="color"]:checked') || {}).value || 0), hours, services: $$('input[name="svc"]:checked', form).map(i => i.value) };
    if (await save('staff', id, data, prev ? 'Specialist updated' : 'Specialist added')) closeDrawer();
  },
  settings: async form => {
    const F = n => form.elements.namedItem(n);
    const open = +F('open').value, close = +F('close').value;
    if (close <= open) { toast('The calendar must end after it starts.'); return; }
    const data = { ...(S.settings || {}), name: F('name').value.trim() || 'Your studio', tagline: F('tagline').value.trim(), address: F('address').value.trim(), phone: F('phone').value.trim(), currency: F('currency').value.trim(), countryCode: digits(F('countryCode').value), open, close, step: +F('step').value, bookingDays: +F('bookingDays').value, leadMin: +F('leadMin').value, cancelHours: +F('cancelHours').value, defaultLang: F('defaultLang').value, timezone: F('timezone').value.trim() || 'Asia/Yerevan',
      reminder: Object.fromEntries(['hy', 'ru', 'en'].map(l => [l, F('reminder_' + l).value.trim() || DEFAULTS.reminder[l]])),
      i18n: Object.fromEntries(['hy', 'ru'].map(l => [l, { name: F('name_' + l).value.trim(), tagline: F('tagline_' + l).value.trim(), address: F('address_' + l).value.trim() }])) };
    await save('settings', 'studio', data, 'Settings saved');
  },
};

/* ================= events ================= */
let drag = null, suppressClick = false;
const ghost = document.createElement('div'); ghost.className = 'slot-hint'; ghost.hidden = true;

document.addEventListener('click', e => {
  if (suppressClick && e.target.closest('.appt')) { e.preventDefault(); return; }
  if (!e.target.closest('.combo')) $$('.combo-list').forEach(l => { l.hidden = true; });
  const el = e.target.closest('[data-act]');
  if (el) {
    if (el.disabled) return;
    const fn = ACT[el.dataset.act];
    if (fn) { if (el.tagName !== 'A') e.preventDefault(); fn(el, e); }
    return;
  }
  const col = e.target.closest('.cal-col');
  if (col) {
    const r = col.getBoundingClientRect(); const c = cfg();
    const m = c.open + Math.floor((e.clientY - r.top) / SLOT) * 15;
    openDrawer({ kind: 'apptForm', seed: { staffId: col.dataset.staff, date: S.date, start: Math.min(m, c.close - 15) } });
  }
});

document.addEventListener('input', e => {
  const t = e.target;
  if (t.id === 'client-q') { S.cq = t.value; render(); return; }
  const form = t.closest && t.closest('form[data-form="appt"]');
  if (form) {
    if (t.name === 'clientName') clientCombo(form, t.value);
    else if (t.name === 'phone') updateClientHint(form);
    if (t.name === 'price' || t.name === 'note' || t.name === 'clientName' || t.name === 'phone') apptRecalc(form, t);
  }
});

document.addEventListener('change', e => {
  const t = e.target;
  if (t.id === 'cal-date') { if (t.value) { S.date = t.value; render(); } return; }
  if (t.dataset && t.dataset.day !== undefined) {
    const form = t.form; const w = t.dataset.day;
    form.elements.namedItem('s' + w).disabled = !t.checked; form.elements.namedItem('e' + w).disabled = !t.checked; return;
  }
  const form = t.closest && t.closest('form[data-form="appt"]');
  if (form) apptRecalc(form, t);
});

document.addEventListener('submit', e => {
  const form = e.target.closest('form[data-form]'); if (!form) return;
  e.preventDefault();
  const h = FORMS[form.dataset.form]; if (!h || form.dataset.busy) return;
  form.dataset.busy = '1';
  Promise.resolve(h(form)).finally(() => { delete form.dataset.busy; });
});

document.addEventListener('keydown', e => {
  if (e.key === 'Tab' && S.drawer) {
    const items = $$('#drawer a[href], #drawer button:not([disabled]), #drawer input:not([type="hidden"]):not([disabled]), #drawer select:not([disabled]), #drawer textarea:not([disabled])').filter(el => el.offsetParent !== null);
    if (items.length) {
      const firstEl = items[0], lastEl = items[items.length - 1];
      if (!$('#drawer').contains(document.activeElement)) { e.preventDefault(); firstEl.focus(); }
      else if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
      else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
    }
  }
  if (e.key === 'Escape') {
    const open = $$('.combo-list').find(l => !l.hidden);
    if (open) { open.hidden = true; return; }
    if (S.drawer) { closeDrawer(); return; }
  }
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '');
  if (S.user && !typing && !S.drawer && S.view === 'calendar' && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (e.key === 'ArrowLeft') { S.date = addDays(S.date, -1); render(); }
    else if (e.key === 'ArrowRight') { S.date = addDays(S.date, 1); render(); }
    else if (e.key === 't' || e.key === 'T') { S.date = todayISO(); S.scrolledFor = null; render(); }
  }
});

document.addEventListener('pointerover', e => {
  const ro = $('#readout'); if (!ro) return;
  const tip = e.target.closest && e.target.closest('[data-tip]');
  ro.textContent = tip ? tip.dataset.tip : ro.dataset.def;
});

// Hover a free quarter hour to see the time a click would book.
document.addEventListener('pointermove', e => {
  if (drag || e.pointerType !== 'mouse') return;
  const col = e.target.closest && e.target.closest('.cal-col');
  if (!col || e.target.closest('.appt')) { ghost.hidden = true; return; }
  const r = col.getBoundingClientRect(); const c = cfg();
  const idx = Math.floor((e.clientY - r.top) / SLOT);
  if (idx < 0 || c.open + idx * 15 >= c.close) { ghost.hidden = true; return; }
  if (ghost.parentNode !== col) col.appendChild(ghost);
  ghost.style.top = `${idx * SLOT}px`; ghost.textContent = `+ ${hm(c.open + idx * 15)}`; ghost.hidden = false;
});

// Drag an appointment to another time or specialist (mouse and pen).
document.addEventListener('pointerdown', e => {
  const el = e.target.closest && e.target.closest('.appt');
  if (!el || e.button !== 0 || e.pointerType === 'touch') return;
  const a = S.appts.find(x => x.id === el.dataset.id); if (!a) return;
  drag = { a, el, col: el.closest('.cal-col'), x0: e.clientX, y0: e.clientY, moved: false, staffId: a.staffId, start: a.start, cols: null };
});
window.addEventListener('pointermove', e => {
  if (!drag) return;
  const dx = e.clientX - drag.x0, dy = e.clientY - drag.y0;
  if (!drag.moved) {
    if (Math.hypot(dx, dy) < 6) return;
    drag.moved = true; ghost.hidden = true;
    drag.el.classList.add('dragging'); document.body.classList.add('is-dragging');
    drag.cols = $$('.cal-col').map(c => ({ c, r: c.getBoundingClientRect() }));
  }
  const c = cfg();
  let start = drag.a.start + Math.round(dy / SLOT) * 15;
  start = Math.max(c.open, Math.min(c.close - Math.min(drag.a.duration, c.close - c.open), start));
  const home = drag.cols.find(o => o.c === drag.col);
  const hit = drag.cols.find(o => e.clientX >= o.r.left && e.clientX < o.r.right) || home;
  drag.el.style.transform = `translate(${hit.r.left - home.r.left}px, ${(start - drag.a.start) / 15 * SLOT}px)`;
  drag.staffId = hit.c.dataset.staff; drag.start = start;
  const t = drag.el.querySelector('.t'); if (t) t.textContent = hm(start);
});
async function endDrag(cancelled) {
  if (!drag) return;
  const d = drag; drag = null;
  document.body.classList.remove('is-dragging');
  if (!d.moved) return;
  suppressClick = true; setTimeout(() => { suppressClick = false; }, 0);
  if (cancelled || (d.staffId === d.a.staffId && d.start === d.a.start)) { render(); return; }
  const clash = S.appts.find(x => x.id !== d.a.id && x.staffId === d.staffId && x.date === d.a.date && x.status !== 'cancelled' && d.start < x.start + x.duration && d.start + d.a.duration > x.start);
  if (clash) { toast(`That overlaps ${first(clash.clientName)}'s ${hm(clash.start)} appointment.`); render(); return; }
  const prev = { ...d.a };
  const ok = await save('appointments', d.a.id, { ...d.a, staffId: d.staffId, start: d.start });
  render();
  if (ok) toast(`Moved to ${hm(d.start)} with ${first(staffById(d.staffId)?.name)}`, { label: 'Undo', fn: () => save('appointments', prev.id, prev) });
}
window.addEventListener('pointerup', () => { endDrag(false); if (deferred) { deferred = false; render(); } });
window.addEventListener('pointercancel', () => { endDrag(true); });

setInterval(() => { if (S.view === 'calendar' && S.date === todayISO() && !S.drawer) scheduleRender(); }, 60e3);

render();
boot();
})();
