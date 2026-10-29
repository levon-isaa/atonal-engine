// Rota public booking site: pick services, a specialist and a time, then manage the booking
// later from its private link. Talks to /api/public/*; never sees other clients' details.
(() => {
'use strict';

const I = window.ROTA_I18N;
const LANGS = ['hy', 'ru', 'en'];
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ESC[c]);
const pad = n => String(n).padStart(2, '0');
const hm = m => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
// Dates travel as YYYY-MM-DD in studio time. Treat them as UTC days so the visitor's own time zone never shifts them.
const toDate = iso => new Date(iso + 'T00:00:00Z');
const addDays = (iso, n) => { const d = toDate(iso); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const weekday = iso => toDate(iso).getUTCDay();
const local = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: nothing to keep */ } },
};

const freshBook = (extra = {}) => ({ step: 1, services: [], staffId: null, fromStaff: null, resched: null, date: null, time: null,
  name: '', phone: '', note: '', errors: {}, error: '', busy: false, done: null, ...extra });
const S = {
  lang: null, studio: null, loadedAt: 0, fatal: null,
  tab: 'book', cat: null, b: freshBook(),
  next: { key: null, data: null, loading: false },
  av: { key: null, data: null, loading: false },
  mine: { loaded: false, loading: false, items: [] },
  highlight: null, confirm: null,
};
{
  const saved = local.get('rota-lang', null);
  const nav = (navigator.language || '').slice(0, 2);
  S.lang = LANGS.includes(saved) ? saved : LANGS.includes(nav) ? nav : null;
}

// ---------------------------------------------------------------- language
const lang = () => S.lang || 'hy';
const loc = () => I.locales[lang()];
const fill = (s, vars) => String(s).replace(/\{(\w+)\}/g, (m, k) => (vars && k in vars ? vars[k] : m));
function t(key, vars) {
  let s = I[lang()][key] ?? I.en[key] ?? key;
  if (typeof s === 'object') s = s.other;
  return fill(s, vars);
}
function tn(key, n, vars) {
  const forms = I[lang()][key] || I.en[key];
  const rule = new Intl.PluralRules(loc()).select(n);
  return fill(forms[rule] || forms.other, { n, ...vars });
}
const tr = (doc, field) => (doc && doc.i18n && doc.i18n[lang()] && doc.i18n[lang()][field]) || (doc && doc[field]) || '';
function fmt(iso, opts) {
  if (lang() !== 'hy') return new Intl.DateTimeFormat(loc(), { timeZone: 'UTC', ...opts }).format(toDate(iso));
  const d = toDate(iso); const D = I.hyDates; const w = d.getUTCDay(); const m = d.getUTCMonth();
  const day = opts.weekday === 'long' ? D.days[w] : opts.weekday === 'short' ? D.daysShort[w] : '';
  const date = opts.month === 'long' ? `${D.months[m]} ${d.getUTCDate()}` : opts.month === 'short' ? `${D.monthsShort[m]} ${d.getUTCDate()}` : '';
  return [day, date].filter(Boolean).join(', ');
}
const dayLong = iso => fmt(iso, { weekday: 'long', day: 'numeric', month: 'long' });
const dayShort = iso => fmt(iso, { weekday: 'short', day: 'numeric', month: 'short' });
const wd = iso => fmt(iso, { weekday: 'short' });
function dayName(iso, lower = false) {
  const n = now();
  if (iso === n.date) return t(lower ? 'today' : 'Today');
  if (iso === addDays(n.date, 1)) return t(lower ? 'tomorrow' : 'Tomorrow');
  return dayShort(iso);
}
const money = n => `${Math.round(+n || 0).toLocaleString(loc())} ${S.studio ? S.studio.currency || '' : ''}`;
function dur(m) { const h = Math.floor(m / 60), r = m % 60; return h ? `${h} ${t('h')}${r ? ` ${r} ${t('min')}` : ''}` : `${r} ${t('min')}`; }
function now() {
  const s = S.studio; let min = s.nowMin + Math.floor((Date.now() - S.loadedAt) / 60000); let date = s.today;
  while (min >= 1440) { min -= 1440; date = addDays(date, 1); }
  return { date, min };
}
const svcById = id => S.studio.services.find(s => s.id === id);
const staffById = id => S.studio.staff.find(s => s.id === id);
const initials = n => String(n || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0] || '').join('').toUpperCase();
const tint = st => 't' + (((st && st.color) || 0) % 6);

// ---------------------------------------------------------------- icons
const P = {
  scissors: '<circle cx="6" cy="7" r="3"/><circle cx="6" cy="17" r="3"/><path d="M8.6 8.6 20 19M8.6 15.4 20 5"/>',
  comb: '<path d="M3.5 8.5h17v3.5h-17z"/><path d="M6 12v6.5M9 12v6.5M12 12v6.5M15 12v6.5M18 12v6.5"/>',
  polish: '<rect x="7.5" y="10" width="9" height="11" rx="2.5"/><path d="M10 10V6.5h4V10M10.8 6.5V3h2.4v3.5"/>',
  eye: '<path d="M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.6"/>',
  drop: '<path d="M12 3.5s6 6.4 6 10.8a6 6 0 0 1-12 0C6 9.9 12 3.5 12 3.5z"/>',
  spark: '<path d="M12 3v5M12 16v5M3 12h5M16 12h5M6 6l3 3M15 15l3 3M18 6l-3 3M9 15l-3 3"/>',
  phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>',
  left: '<path d="m14.5 6-6 6 6 6"/>', right: '<path d="m9.5 6 6 6-6 6"/>', pin: '<path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
  chat: '<path d="M20.5 11.6a8.4 8.4 0 0 1-12.3 7.4L3.5 20.5l1.5-4.5a8.4 8.4 0 1 1 15.5-4.4z"/>', cal: '<rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/><path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.6c1.9.7 3.1 2.4 3.5 5.4"/>',
};
const icon = (k, cls = 'ic') => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[k]}</svg>`;
function catIcon(cat) {
  const c = String(cat || '').toLowerCase();
  return /barb|beard|men/.test(c) ? 'comb' : /hair/.test(c) ? 'scissors' : /nail|mani|pedi/.test(c) ? 'polish' : /brow|lash|eye/.test(c) ? 'eye' : /skin|face|facial|spa/.test(c) ? 'drop' : 'spark';
}
const categories = () => [...new Set(S.studio.services.map(s => s.category || 'Other'))];
const catTint = cat => 't' + (categories().indexOf(cat) % 6);
const catLabel = cat => { const sv = S.studio.services.find(s => (s.category || 'Other') === cat); return (sv && tr(sv, 'category')) || cat; };

// ---------------------------------------------------------------- network
async function api(path, opts = {}) {
  const method = opts.method || 'GET';
  const headers = method === 'GET' ? {} : { 'X-Rota': '1' };
  if (opts.body) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { method, headers, body: opts.body ? JSON.stringify(opts.body) : undefined, credentials: 'same-origin' });
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) { const err = new Error((data && data.message) || res.statusText); err.status = res.status; err.code = data && data.error; throw err; }
  return data;
}
function errText(e) {
  const map = { slot_taken: 'errTaken', slow_down: 'errSlow', too_many: 'errTooMany', bad_link: 'errLink', not_live: 'errNotLive', bad_phone: 'errPhoneShort', missing_name: 'errName' };
  if (e && e.code === 'too_late') return t('tooLate', { h: S.studio.cancelHours, phone: S.studio.phone || '' });
  return t(map[e && e.code] || 'errGeneric');
}
const tokens = () => local.get('rota-bookings', []).filter(x => typeof x === 'string');
function addToken(tok) { const list = tokens().filter(x => x !== tok); list.unshift(tok); local.set('rota-bookings', list.slice(0, 30)); }
function dropToken(tok) { local.set('rota-bookings', tokens().filter(x => x !== tok)); }
const manageUrl = tok => `${location.origin}/b/${encodeURIComponent(tok)}`;
const mapsUrl = () => `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(tr(S.studio, 'address') || S.studio.address || '')}`;
function waUrl() {
  const raw = String(S.studio.phone || ''); let d = raw.replace(/\D/g, '');
  if (!raw.trim().startsWith('+') && d.startsWith('0') && S.studio.countryCode) d = S.studio.countryCode + d.slice(1);
  return d.length >= 10 ? `https://wa.me/${d}` : '';
}
function calUrl(v) {
  const stamp = m => `${v.date.replace(/-/g, '')}T${pad(Math.floor(m / 60))}${pad(m % 60)}00`;
  const q = new URLSearchParams({ action: 'TEMPLATE', text: `${v.services.map(s => tr(s, 'name')).join(', ')} · ${tr(S.studio, 'name') || S.studio.name}`,
    dates: `${stamp(v.start)}/${stamp(v.start + v.duration)}`, details: `${t('with')}: ${tr(v.staff, 'name')}\n${manageUrl(v.token)}`,
    location: tr(S.studio, 'address') || S.studio.address || '' });
  if (S.studio.timezone) q.set('ctz', S.studio.timezone);
  return `https://calendar.google.com/calendar/render?${q}`;
}

// ---------------------------------------------------------------- render infrastructure
let renderQueued = false;
function render() {
  const root = $('#root');
  const ae = document.activeElement; const id = ae && ae !== document.body && root.contains(ae) ? ae.id : null;
  let sel = null; if (id) { try { if (typeof ae.selectionStart === 'number') sel = [ae.selectionStart, ae.selectionEnd]; } catch (e) { sel = null; } }
  const keep = $$('[data-keep]', root).map(el => [el.dataset.keep, el.scrollLeft]);
  root.innerHTML = view();
  for (const [k, l] of keep) { const el = root.querySelector(`[data-keep="${k}"]`); if (el) el.scrollLeft = l; }
  if (id) { const el = document.getElementById(id); if (el) { el.focus({ preventScroll: true }); if (sel) { try { el.setSelectionRange(sel[0], sel[1]); } catch (e) { /* not text */ } } } }
  afterRender();
}
const schedule = () => { if (!renderQueued) { renderQueued = true; requestAnimationFrame(() => { renderQueued = false; render(); }); } };
let lastKey = null;
function afterRender() {
  const b = S.b; const key = `${S.tab}:${b.step}:${b.done ? 'done' : ''}:${b.resched || ''}`;
  if (lastKey !== null && key !== lastKey) {
    const h = $('#step-title'); if (h) h.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }
  lastKey = key;
  ensureData();
}
function toast(msg) {
  const box = $('#toasts'); const el = document.createElement('div'); el.className = 'toast'; el.textContent = msg; box.appendChild(el);
  while (box.children.length > 2) box.firstChild.remove();
  setTimeout(() => el.remove(), 3800);
}

// Fetch what the current step needs, once per change of choices.
function ensureData() {
  if (!S.studio || S.tab !== 'book') return;
  const b = S.b; const svcs = b.services.join(',');
  if (b.step === 2 && !b.done) {
    const key = svcs;
    if (S.next.key !== key) {
      S.next = { key, data: null, loading: true };
      api(`/api/public/availability?services=${encodeURIComponent(svcs)}&staff=any&days=1&next=1`)
        .then(d => { if (S.next.key === key) { S.next.data = d; S.next.loading = false; schedule(); } })
        .catch(e => { if (S.next.key === key) { S.next.data = { error: errText(e) }; S.next.loading = false; schedule(); } });
    }
  }
  if (b.step === 3 && !b.done) {
    const key = `${svcs}|${b.staffId}|${b.resched || ''}`;
    if (S.av.key !== key) {
      S.av = { key, data: null, loading: true };
      const q = new URLSearchParams({ services: svcs, staff: b.staffId || 'any', from: now().date, days: String(S.studio.bookingDays || 14) });
      if (b.resched) q.set('link', b.resched);
      api(`/api/public/availability?${q}`)
        .then(d => { if (S.av.key === key) { S.av.data = d; S.av.loading = false; schedule(); } })
        .catch(e => { if (S.av.key === key) { S.av.data = { error: errText(e) }; S.av.loading = false; schedule(); } });
    }
  }
}

// ---------------------------------------------------------------- views
function view() {
  if (!S.studio) {
    if (S.fatal) return `<div class="site"><div class="card"><div class="empty"><h2>${t('errGeneric')}</h2><button type="button" class="btn primary" data-act="reload">↻</button></div></div></div>`;
    return `<div class="site"><div class="loading">${t('loading')}</div></div>`;
  }
  if (S.studio.paused) {
    const tel = String(S.studio.phone || '').replace(/[^\d+]/g, '');
    return `<div class="site">${topbar()}<main class="main paused" id="main" tabindex="-1"><section class="card empty" role="status"><h1 id="step-title" tabindex="-1">${esc(tr(S.studio, 'name'))}</h1><p>${t('paused')}</p>${tel ? `<a class="btn primary" href="tel:${esc(tel)}">${icon('phone')}${t('callStudio')} ${esc(S.studio.phone)}</a>` : ''}</section></main></div>`;
  }
  const bar = S.tab === 'book' ? bookBar() : '';
  const main = S.tab === 'mine' ? mineView() : S.tab === 'info' ? infoView() : bookView();
  return `<div class="site${bar ? ' has-bar' : ''}">
    ${topbar()}
    <div class="layout">
      <aside class="side">${hero()}${S.tab === 'book' ? visitCard('side') : ''}</aside>
      <main class="main" id="main" tabindex="-1">${tabs()}${main}${S.studio.demo ? `<p class="foot">${t('demo')} <a href="${esc(location.protocol + '//' + location.host.replace(/^demo\./, '') + '/')}">${t('demoLink')}</a></p>` : S.studio.sample ? `<p class="foot">${t('sample')}</p>` : ''}</main>
    </div>
  </div>${bar}`;
}

function topbar() {
  const name = tr(S.studio, 'name') || S.studio.name;
  return `<header class="topbar"><div class="brand"><span class="brand-mark" aria-hidden="true">${esc(initials(name).slice(0, 1))}</span><span>${esc(name)}</span></div>
    <div class="langs" role="group" aria-label="${esc(t('language'))}">${LANGS.map(l => `<button type="button" id="lang-${l}" data-act="lang" data-l="${l}" lang="${l}" aria-pressed="${lang() === l}" aria-label="${esc(I.names[l])}">${I.short[l]}</button>`).join('')}</div></header>`;
}

function openStatus() {
  const n = now(); const hrs = S.studio.hours || {};
  const h = hrs[String(weekday(n.date))];
  if (h && n.min < h[1]) return n.min >= h[0] ? { on: true, text: t('openNow', { t: hm(h[1]) }) } : { on: false, text: t('opensToday', { t: hm(h[0]) }) };
  for (let i = 1; i <= 7; i++) {
    const d = addDays(n.date, i); const hh = hrs[String(weekday(d))];
    if (hh) return { on: false, text: t('closedOpens', { day: i === 1 ? t('tomorrow') : fmt(d, { weekday: 'long' }), t: hm(hh[0]) }) };
  }
  return { on: false, text: t('closed') };
}

function hero() {
  const s = S.studio; const os = openStatus(); const wa = waUrl(); const address = tr(s, 'address');
  return `<section class="card hero" aria-label="${esc(tr(s, 'name'))}">
    <h1>${esc(tr(s, 'name'))}</h1>${tr(s, 'tagline') ? `<p>${esc(tr(s, 'tagline'))}</p>` : ''}
    <span class="open${os.on ? ' on' : ''}"><i></i>${esc(os.text)}</span>
    <dl>${address ? `<div><dt>${t('address')}</dt><dd>${esc(address)}</dd></div>` : ''}${s.phone ? `<div><dt>${t('phone')}</dt><dd class="num">${esc(s.phone)}</dd></div>` : ''}</dl>
    <div class="quick">${address ? `<a class="btn sm" href="${esc(mapsUrl())}" target="_blank" rel="noopener noreferrer">${icon('pin')}${t('directions')}</a>` : ''}${wa ? `<a class="btn sm" href="${esc(wa)}" target="_blank" rel="noopener noreferrer">${icon('chat')}${t('whatsapp')}</a>` : ''}</div>
  </section>`;
}

function tabs() {
  const upcoming = S.mine.items.filter(x => x.view && x.view.live).length;
  const items = [['book', t('book')], ['mine', `${t('mine')}${upcoming ? ` <span class="count">${upcoming}</span>` : ''}`], ['info', t('info')]];
  return `<nav class="tabs" aria-label="${esc(t('book'))}">${items.map(([k, l]) => `<button type="button" id="tab-${k}" data-act="tab" data-t="${k}"${S.tab === k ? ' aria-current="page"' : ''}>${l}</button>`).join('')}</nav>`;
}

const FLOW = { 1: 'stepServices', 2: 'stepSpecialist', 3: 'stepTime', 4: 'stepDetails' };
const flowOf = b => b.resched ? [3, 4] : b.fromStaff ? [1, 3, 4] : [1, 2, 3, 4];
const bookSvcs = () => S.b.services.map(svcById).filter(Boolean);
const totals = () => { const sv = bookSvcs(); return { dur: sv.reduce((a, s) => a + s.duration, 0), price: sv.reduce((a, s) => a + s.price, 0) }; };

function bookView() {
  const b = S.b;
  if (!S.studio.services.length) return `<div class="card"><div class="empty"><h2>${t('offline')}</h2><p>${t('offlineHint')}</p></div></div>`;
  if (b.done) return doneView(b.done);
  const flow = flowOf(b); const at = Math.max(0, flow.indexOf(b.step));
  const name = n => b.resched && n === 4 ? t('stepConfirm') : t(FLOW[n]);
  const steps = `<ol class="steps" aria-label="${esc(t('book'))}">${flow.map((n, i) => `<li class="${i < at ? 'done' : i === at ? 'on' : ''}"${i === at ? ' aria-current="step"' : ''}>${i < at ? `<button type="button" class="step" data-act="goto" data-step="${n}"><span class="lbl">${name(n)}</span></button>` : `<span class="step"><span class="lbl">${name(n)}</span></span>`}</li>`).join('')}</ol>`;
  const back = at > 0 || b.resched ? `<button type="button" class="btn ghost sm" id="bk-back" data-act="back">${icon('left')}${b.resched && at === 0 ? t('keepTime') : t('back')}</button>` : '';
  const err = b.error ? `<p class="form-err" role="alert">${esc(b.error)}</p>` : '';
  let body = '';
  if (b.step === 1) body = stepServices();
  else if (b.step === 2) body = stepStaff(back);
  else if (b.step === 3) body = stepTime(back);
  else body = stepDetails(back, err);
  const upNote = b.step === 1 && !b.resched ? (() => {
    const up = S.mine.items.map(x => x.view).filter(v => v && v.live).sort((x, y) => (x.date + hm(x.start)).localeCompare(y.date + hm(y.start)))[0];
    return up ? `<div class="note-ok"><span>${esc(t('youreBookedFor', { when: `${dayName(up.date, true)}, ${hm(up.start)}` }))}</span><button type="button" class="btn sm" data-act="tab" data-t="mine">${t('manage')}</button></div>` : '';
  })() : '';
  return `${upNote}${steps}${b.step !== 4 ? err : ''}${body}`;
}

function stepServices() {
  const b = S.b; const from = b.fromStaff ? staffById(b.fromStaff) : null;
  const offered = S.studio.services.filter(s => !from || (from.services || []).includes(s.id));
  const cats = [...new Set(offered.map(s => s.category || 'Other'))];
  const cat = cats.includes(S.cat) ? S.cat : null;
  const withChip = from ? `<span class="with"><span class="av sm ${tint(from)}">${esc(initials(tr(from, 'name')))}</span>${esc(t('withX', { name: tr(from, 'name').split(' ')[0] }))}<button type="button" class="x" id="with-clear" data-act="with-clear" aria-label="${esc(t('anyone'))}">${icon('x')}</button></span>` : '';
  const strip = from ? '' : `<div class="team-strip" role="group" aria-label="${esc(t('bookWith'))}" data-keep="team">${S.studio.staff.filter(st => (st.services || []).some(id => offered.some(s => s.id === id))).map(st => `<button type="button" class="team-chip" id="bw-${esc(st.id)}" data-act="with" data-id="${esc(st.id)}"><span class="av ${tint(st)}">${esc(initials(tr(st, 'name')))}</span><span><b>${esc(tr(st, 'name').split(' ')[0])}</b><small>${esc(tr(st, 'role'))}</small></span></button>`).join('')}</div>`;
  const chips = cats.length > 1 ? `<div class="cats" role="group" aria-label="${esc(t('stepServices'))}" data-keep="cats"><button type="button" class="all" id="cat-all" data-act="cat" data-c="" aria-pressed="${!cat}">${t('all')}</button>${cats.map(c => `<button type="button" id="cat-${esc(c.replace(/\W+/g, '-'))}" data-act="cat" data-c="${esc(c)}" aria-pressed="${cat === c}" class="${catTint(c)}"><span class="cat-ic">${icon(catIcon(c))}</span>${esc(catLabel(c))}</button>`).join('')}</div>` : '';
  const groups = (cat ? [cat] : cats).map(c => {
    const items = offered.filter(s => (s.category || 'Other') === c);
    return `${cats.length > 1 && !cat ? `<h3 class="group-h">${esc(catLabel(c))}</h3>` : ''}<div class="list">${items.map(s => `<label class="svc ${catTint(c)}" for="sv-${esc(s.id)}">
      <input type="checkbox" class="sr" id="sv-${esc(s.id)}" data-svc value="${esc(s.id)}"${b.services.includes(s.id) ? ' checked' : ''}>
      <span class="svc-ic">${icon(catIcon(c))}</span>
      <span class="svc-main"><b>${esc(tr(s, 'name'))}</b>${tr(s, 'description') ? `<small>${esc(tr(s, 'description'))}</small>` : ''}<span class="svc-dur">${dur(s.duration)}</span></span>
      <span class="svc-end"><b class="num">${money(s.price)}</b><span class="tick">${icon(b.services.includes(s.id) ? 'check' : 'plus')}</span></span>
    </label>`).join('')}</div>`;
  }).join('');
  return `<div class="head"><h2 id="step-title" tabindex="-1">${t('chooseServices')}</h2>${withChip}</div>
    ${strip ? `<p class="sub">${t('bookWith')}</p>${strip}` : ''}${chips}${groups}`;
}

function stepStaff(back) {
  const head = `<div class="head"><h2 id="step-title" tabindex="-1">${t('chooseSpecialist')}</h2>${back}</div>${visitCard('inline')}`;
  if (S.next.loading || !S.next.data) return `${head}<div class="loading">${t('loading')}</div>`;
  if (S.next.data.error) return `${head}<p class="form-err" role="alert">${esc(S.next.data.error)}</p>`;
  const el = S.next.data.staff.map(staffById).filter(Boolean);
  if (!el.length) return `${head}<div class="card"><div class="empty"><h2>${t('noOne')}</h2><p>${t('noOneHint')}</p></div></div>`;
  const next = S.next.data.next || {};
  const nx = id => next[id] ? `<span class="pro-next">${t('nextFree')}<b>${esc(dayName(next[id].date))}, ${hm(next[id].t)}</b></span>` : `<span class="pro-next">${t('fullyBooked')}</span>`;
  return `${head}<div class="list">
    ${el.length > 1 ? `<button type="button" class="pro" id="pro-any" data-act="staff" data-id="any"><span class="any">${icon('users')}</span><span class="pro-main"><b>${t('anySpecialist')}</b><small>${t('anyHint')}</small></span>${icon('right')}</button>` : ''}
    ${el.map(st => `<button type="button" class="pro" id="pro-${esc(st.id)}" data-act="staff" data-id="${esc(st.id)}"><span class="av lg ${tint(st)}">${esc(initials(tr(st, 'name')))}</span><span class="pro-main"><b>${esc(tr(st, 'name'))}</b><small>${esc(tr(st, 'bio') || tr(st, 'role'))}</small></span>${nx(st.id)}</button>`).join('')}
  </div>`;
}

function stepTime(back) {
  const b = S.b;
  const head = `<div class="head"><h2 id="step-title" tabindex="-1">${b.resched ? t('pickNewTime') : t('chooseTime')}</h2>${back}</div>`;
  const lead = b.resched && b.reschedFrom ? `<p class="sub">${esc(t('nowBooked', { when: `${dayName(b.reschedFrom.date, true)}, ${hm(b.reschedFrom.start)}` }))}</p>` : '';
  if (S.av.loading || !S.av.data) return `${head}${lead}${visitCard('inline')}<div class="loading">${t('loading')}</div>`;
  if (S.av.data.error) return `${head}${lead}<p class="form-err" role="alert">${esc(S.av.data.error)}</p>`;
  const days = S.av.data.days;
  let sel = days.find(d => d.date === b.date && d.slots.length) || days.find(d => d.slots.length);
  if (!sel) return `${head}${lead}${visitCard('inline')}<div class="card"><div class="empty"><h2>${t('noFreeTime', { n: S.studio.bookingDays })}</h2>${b.resched ? '' : `<p>${t('tryAnother')}</p>`}</div></div>`;
  const n = now();
  const pills = days.map(d => `<button type="button" class="day" id="day-${d.date}" data-act="day" data-d="${d.date}" aria-pressed="${d.date === sel.date}" aria-label="${esc(dayLong(d.date))}: ${esc(d.slots.length ? tn('nFree', d.slots.length) : d.state === 'closed' ? t('dayClosed') : t('dayFull'))}"${d.slots.length ? '' : ' disabled'}>
      <span class="w">${esc(d.date === n.date ? t('Today') : wd(d.date))}</span><span class="n">${toDate(d.date).getUTCDate()}</span>
      <span class="m">${esc(d.slots.length ? tn('nFree', d.slots.length) : d.state === 'closed' ? t('dayClosed') : t('dayFull'))}</span></button>`).join('');
  const groups = [['morning', 0, 720], ['afternoon', 720, 1020], ['evening', 1020, 1440]]
    .map(([k, a, z]) => [k, sel.slots.filter(s => s.t >= a && s.t < z)]).filter(g => g[1].length);
  return `${head}${lead}${visitCard('inline')}
    <div class="days" role="group" aria-label="${esc(t('stepTime'))}" data-keep="days">${pills}</div>
    <div class="card slots-card"><p class="date-h">${esc(dayLong(sel.date))}</p>
      ${groups.map(([k, list]) => `<div class="slot-group"><span>${t(k)}</span><div class="slots">${list.map(s => `<button type="button" class="slot num" id="t-${s.t}" data-act="time" data-d="${sel.date}" data-t="${s.t}">${hm(s.t)}</button>`).join('')}</div></div>`).join('')}
    </div>`;
}

function stepDetails(back, err) {
  const b = S.b; const { dur: d, price } = totals(); const fe = b.errors || {};
  const st = b.staffId && b.staffId !== 'any' ? staffById(b.staffId) : null;
  const who = st ? tr(st, 'name') : t('anySpecialist');
  const summary = `<div class="card summary"><dl class="kv">
      ${b.resched && b.reschedFrom ? `<dt>${t('was')}</dt><dd><s>${esc(dayShort(b.reschedFrom.date))}, ${hm(b.reschedFrom.start)}</s></dd>` : ''}
      <dt>${t('when')}</dt><dd><span class="cap" style="display:inline-block">${esc(dayLong(b.date))}</span>, <span class="num">${hm(b.time)}–${hm(b.time + d)}</span></dd>
      <dt>${t('with')}</dt><dd>${esc(who)}</dd>
      <dt>${t('services')}</dt><dd>${esc(bookSvcs().map(s => tr(s, 'name')).join(', '))}</dd>
      <dt>${t('total')}</dt><dd class="num">${money(price)} · ${dur(d)}</dd></dl></div>`;
  if (b.resched) {
    return `<div class="head"><h2 id="step-title" tabindex="-1">${t('confirmNewTime')}</h2>${back}</div>${summary}
      <form class="card form" data-form="book" novalidate>${err}<button class="btn primary lg wide" id="bk-confirm"${b.busy ? ' disabled' : ''}>${b.busy ? t('saving') : t('moveVisit')}</button></form>`;
  }
  return `<div class="head"><h2 id="step-title" tabindex="-1">${t('yourDetails')}</h2>${back}</div>${summary}
    <form class="card form" data-form="book" novalidate>
      <div class="field"><label for="bk-name">${t('name')}</label><input id="bk-name" data-f="name" value="${esc(b.name)}" autocomplete="name" required${fe.name ? ' aria-invalid="true"' : ''} aria-describedby="bk-name-err"><small class="field-err" id="bk-name-err">${esc(fe.name || '')}</small></div>
      <div class="field"><label for="bk-phone">${t('phone')}</label><input id="bk-phone" type="tel" data-f="phone" value="${esc(b.phone)}" autocomplete="tel" inputmode="tel" placeholder="+374 91 234 567" required${fe.phone ? ' aria-invalid="true"' : ''} aria-describedby="bk-phone-err bk-phone-help"><small class="field-err" id="bk-phone-err">${esc(fe.phone || '')}</small><small id="bk-phone-help">${t('phoneHelp')}</small></div>
      <div class="field"><label for="bk-note">${t('note')} <span class="muted">(${t('optional')})</span></label><textarea id="bk-note" data-f="note" rows="2">${esc(b.note)}</textarea></div>
      <div class="hp" aria-hidden="true"><label for="bk-web">Website</label><input id="bk-web" name="website" tabindex="-1" autocomplete="off"></div>
      <p class="consent">${t('consent')}</p>
      ${err}
      <button class="btn primary lg wide" id="bk-confirm"${b.busy ? ' disabled' : ''}>${b.busy ? t('booking') : t('confirm', { price: money(price) })}</button>
    </form>`;
}

function visitCard(where) {
  const b = S.b;
  if (b.done || b.step >= 4 || !b.services.length || (where === 'inline' && b.step === 1)) return '';
  const { dur: d, price } = totals();
  const st = b.staffId && b.staffId !== 'any' ? staffById(b.staffId) : null;
  const later = `<span class="muted">${t('notChosen')}</span>`;
  const rows = [[t('services'), esc(bookSvcs().map(s => tr(s, 'name')).join(', ')), b.resched ? 0 : 1],
    [t('with'), st ? esc(tr(st, 'name')) : b.staffId === 'any' ? t('anySpecialist') : later, b.resched ? 0 : b.fromStaff ? 1 : 2]];
  if (b.step >= 3) rows.push([t('when'), b.time != null && b.date ? `${esc(dayShort(b.date))}, ${hm(b.time)}` : later, 0]);
  return `<section class="card visit ${where}" aria-label="${esc(t('yourVisit'))}"><h2>${t('yourVisit')}</h2><dl>${rows.map(([k, v, go]) => `<div class="vrow"><dt>${k}</dt><dd><span>${v}</span>${go && go < b.step ? `<button type="button" class="linkish" data-act="goto" data-step="${go}">${t('change')}<span class="sr"> ${esc(k)}</span></button>` : ''}</dd></div>`).join('')}</dl>
    <div class="vtotal"><span>${dur(d)}</span><span class="num">${money(price)}</span></div></section>`;
}

function bookBar() {
  const b = S.b;
  if (b.step !== 1 || b.done || !S.studio.services.length) return '';
  const { dur: d, price } = totals();
  return `<div class="bar"><div class="bar-in"><span class="bar-pad"></span><div class="bar-row">
    <div class="bar-sum">${b.services.length ? `${esc(tn('nServices', b.services.length))} · ${dur(d)}<b class="num">${money(price)}</b>` : `${t('pickServices')}<b>&nbsp;</b>`}</div>
    <button type="button" class="btn primary" id="bk-next" data-act="next"${b.services.length ? '' : ' disabled'}>${t('continue')}${icon('right')}</button></div></div></div>`;
}

function doneView(v) {
  const wa = waUrl(); const url = manageUrl(v.token);
  return `<div class="card done-card">
    <span class="big">${icon('check')}</span>
    <h2 id="step-title" tabindex="-1">${v.moved ? t('moved') : t('booked')}</h2>
    <p>${esc(t('doneLine', { date: dayLong(v.date), time: hm(v.start), name: tr(v.staff, 'name') }))}</p>
    <dl class="kv"><dt>${t('services')}</dt><dd>${esc(v.services.map(s => tr(s, 'name')).join(', '))}</dd><dt>${t('duration')}</dt><dd>${dur(v.duration)}</dd><dt>${t('total')}</dt><dd class="num">${money(v.price)}</dd>${tr(S.studio, 'address') ? `<dt>${t('where')}</dt><dd>${esc(tr(S.studio, 'address'))}</dd>` : ''}</dl>
    <div class="linkbox"><b>${t('yourLink')}</b><code id="manage-url">${esc(url)}</code><small>${t('linkHelp')}</small>
      <div class="row"><button type="button" class="btn sm" id="copy-link" data-act="copy" data-text="${esc(url)}">${icon('copy')}${t('copyLink')}</button><a class="btn sm" href="${esc(calUrl(v))}" target="_blank" rel="noopener noreferrer">${icon('cal')}${t('addCal')}</a>${wa ? `<a class="btn sm" href="${esc(wa)}" target="_blank" rel="noopener noreferrer">${icon('chat')}${t('msgStudio')}</a>` : ''}</div></div>
    <div class="row center"><button type="button" class="btn primary" id="bk-mine" data-act="tab" data-t="mine">${t('seeMine')}</button><button type="button" class="btn" id="bk-again" data-act="reset">${t('bookAnother')}</button></div>
  </div>`;
}

function bookingCard(item) {
  const v = item.view;
  if (!v) return item.error ? `<div class="card bk past"><p>${esc(item.error)}</p></div>` : '';
  const st = { booked: ['stBooked', 'accent'], confirmed: ['stConfirmed', 'ok'], arrived: ['stVisited', 'ok'], noshow: ['stMissed', 'warn'] }[v.status] || ['stPast', ''];
  const label = v.status === 'cancelled' ? [v.cancelledBy === 'client' ? 'stCancelledYou' : 'stCancelledStudio', 'bad'] : v.live ? st : v.status === 'booked' || v.status === 'confirmed' ? ['stPast', ''] : st;
  let acts = '';
  if (v.live) {
    if (S.confirm === v.token) {
      acts = `<div class="confirm" role="alert">${esc(t('cancelQ', { when: `${dayName(v.date, true)}, ${hm(v.start)}` }))}<div class="row"><button type="button" class="btn danger solid sm" id="cancel-yes" data-act="cancel-yes" data-tok="${esc(v.token)}">${t('cancelYes')}</button><button type="button" class="btn ghost sm" data-act="cancel-no">${t('keep')}</button></div></div>`;
    } else {
      acts = `<div class="row">${v.canChange ? `<button type="button" class="btn sm" data-act="resched" data-tok="${esc(v.token)}">${t('changeTime')}</button><button type="button" class="btn sm ghost danger" data-act="cancel" data-tok="${esc(v.token)}">${t('cancel')}</button>` : `<span class="muted">${esc(t('tooLate', { h: v.cancelHours, phone: S.studio.phone || '' }))}</span>`}<a class="btn sm ghost" href="${esc(calUrl(v))}" target="_blank" rel="noopener noreferrer">${icon('cal')}${t('addCal')}</a></div>`;
    }
  }
  return `<article class="card bk${v.live ? '' : ' past'}${S.highlight === v.token ? ' hl' : ''}">
    <div class="bk-top"><div><b>${esc(dayLong(v.date))}</b><span class="num">${hm(v.start)}–${hm(v.start + v.duration)}</span></div><span class="pill ${label[1]}">${t(label[0])}</span></div>
    <p>${esc(v.services.map(s => tr(s, 'name')).join(', '))} · ${esc(tr(v.staff, 'name'))} · <span class="num">${money(v.price)}</span></p>
    ${acts}
  </article>`;
}

function mineView() {
  const head = `<div class="head"><h2 id="step-title" tabindex="-1">${t('mine')}</h2></div>`;
  if (!S.mine.loaded) return `${head}<div class="loading">${t('loading')}</div>`;
  const items = S.mine.items.filter(x => x.view || x.error);
  if (!items.length) return `${head}<div class="card"><div class="empty"><h2>${t('noBookings')}</h2><p>${t('noBookingsHint')}</p><button type="button" class="btn primary" data-act="tab" data-t="book">${t('book')}</button></div></div>`;
  const key = v => v.date + hm(v.start);
  const up = items.filter(x => x.view && x.view.live).sort((a, b) => key(a.view).localeCompare(key(b.view)));
  const past = items.filter(x => !(x.view && x.view.live)).sort((a, b) => (b.view ? key(b.view) : '').localeCompare(a.view ? key(a.view) : ''));
  return `${head}${up.length ? `<h3 class="group-h">${t('upcoming')}</h3>${up.map(bookingCard).join('')}` : ''}${past.length ? `<h3 class="group-h">${t('pastCancelled')}</h3>${past.slice(0, 12).map(bookingCard).join('')}` : ''}`;
}

function infoView() {
  const s = S.studio; const n = now(); const wa = waUrl(); const address = tr(s, 'address');
  const order = [1, 2, 3, 4, 5, 6, 0];
  const rows = order.map(w => { const d = addDays(n.date, (w - weekday(n.date) + 7) % 7); const h = (s.hours || {})[String(w)]; return `<tr${w === weekday(n.date) ? ' class="today"' : ''}><th scope="row">${esc(fmt(d, { weekday: 'long' }))}</th><td class="num">${h ? `${hm(h[0])}–${hm(h[1])}` : esc(t('closed'))}</td></tr>`; }).join('');
  return `<div class="head"><h2 id="step-title" tabindex="-1">${t('info')}</h2></div>
    <section class="card info"><h2>${t('hours')}</h2><table class="hours">${rows}</table></section>
    <section class="card info"><h2>${t('findUs')}</h2><dl class="kv">${address ? `<dt>${t('address')}</dt><dd>${esc(address)}</dd>` : ''}${s.phone ? `<dt>${t('phone')}</dt><dd class="num">${esc(s.phone)}</dd>` : ''}</dl>
      <div class="row">${address ? `<a class="btn sm" href="${esc(mapsUrl())}" target="_blank" rel="noopener noreferrer">${icon('pin')}${t('directions')}</a>` : ''}${wa ? `<a class="btn sm" href="${esc(wa)}" target="_blank" rel="noopener noreferrer">${icon('chat')}${t('whatsapp')}</a>` : ''}${s.phone ? `<button type="button" class="btn sm ghost" data-act="copy" data-text="${esc(s.phone)}">${icon('copy')}${t('copyNumber')}</button>` : ''}</div></section>
    <section class="card info"><h2>${t('team')}</h2>${s.staff.map(st => `<div class="member"><span class="av lg ${tint(st)}">${esc(initials(tr(st, 'name')))}</span><div class="grow"><b>${esc(tr(st, 'name'))}</b><small>${esc(tr(st, 'role'))}</small>${tr(st, 'bio') ? `<p>${esc(tr(st, 'bio'))}</p>` : ''}</div><button type="button" class="btn sm" data-act="with" data-id="${esc(st.id)}">${t('bookWithX')}</button></div>`).join('')}</section>`;
}

// ---------------------------------------------------------------- my bookings
async function loadMine() {
  if (S.mine.loading) return;
  S.mine.loading = true;
  const list = tokens();
  const results = await Promise.allSettled(list.map(tok => api(`/api/public/bookings/${encodeURIComponent(tok)}`)));
  S.mine.items = results.map((r, i) => {
    if (r.status === 'fulfilled') return { token: list[i], view: r.value };
    if (r.reason && r.reason.status === 404) { dropToken(list[i]); return { token: list[i], error: list[i] === S.highlight ? t('errLink') : '' }; }
    return { token: list[i], error: t('errGeneric') };
  });
  S.mine.loaded = true; S.mine.loading = false;
  render();
}
function replaceMine(v) {
  const i = S.mine.items.findIndex(x => x.token === v.token);
  if (i >= 0) S.mine.items[i] = { token: v.token, view: v }; else S.mine.items.unshift({ token: v.token, view: v });
}

// ---------------------------------------------------------------- booking
async function submitBooking() {
  const b = S.b;
  if (!b.resched) {
    b.name = ($('#bk-name')?.value ?? b.name).trim(); b.phone = ($('#bk-phone')?.value ?? b.phone).trim(); b.note = ($('#bk-note')?.value ?? b.note).trim();
    const fe = {};
    if (!b.name) fe.name = t('errName');
    if (!b.phone) fe.phone = t('errPhone');
    else if (b.phone.replace(/\D/g, '').length < 8) fe.phone = t('errPhoneShort');
    b.errors = fe;
    if (fe.name || fe.phone) { b.error = ''; render(); $(fe.name ? '#bk-name' : '#bk-phone')?.focus(); return; }
  }
  b.error = ''; b.busy = true; render();
  try {
    if (b.resched) {
      const v = await api(`/api/public/bookings/${encodeURIComponent(b.resched)}/reschedule`, { method: 'POST', body: { date: b.date, start: b.time } });
      replaceMine(v); b.done = { ...v, moved: true };
    } else {
      const v = await api('/api/public/bookings', { method: 'POST', body: { services: b.services, staff: b.staffId || 'any', date: b.date, start: b.time,
        name: b.name, phone: b.phone, note: b.note, lang: lang(), website: $('#bk-web')?.value || '' } });
      addToken(v.token); local.set('rota-profile', { name: b.name, phone: b.phone }); replaceMine(v); b.done = v;
    }
    S.av.key = null; S.next.key = null;
  } catch (e) {
    b.error = errText(e);
    if (e.code === 'slot_taken') { b.step = 3; b.time = null; S.av.key = null; }
  }
  b.busy = false; render();
}

// ---------------------------------------------------------------- actions
const ACT = {
  reload: () => location.reload(),
  lang: el => { S.lang = el.dataset.l; local.set('rota-lang', S.lang); applyLang(); render(); },
  tab: el => { S.tab = el.dataset.t; S.confirm = null; if (S.b.done) S.b = freshBook(profile()); if (S.tab === 'mine') { S.mine.loaded = S.mine.loaded && false; loadMine(); } render(); },
  cat: el => { S.cat = el.dataset.c || null; render(); },
  with: el => { S.b = freshBook({ ...profile(), fromStaff: el.dataset.id, staffId: el.dataset.id }); S.tab = 'book'; S.cat = null; render(); },
  'with-clear': () => { Object.assign(S.b, { fromStaff: null, staffId: null, services: [] }); render(); },
  next: () => { if (S.b.services.length) { S.b.step = S.b.fromStaff ? 3 : 2; S.b.error = ''; render(); } },
  back: () => {
    const b = S.b; const flow = flowOf(b); const i = flow.indexOf(b.step);
    if (i <= 0) { if (b.resched) { S.b = freshBook(profile()); S.tab = 'mine'; } }
    else { b.step = flow[i - 1]; b.error = ''; if (b.step < 3) b.time = null; }
    render();
  },
  goto: el => {
    const b = S.b; const n = +el.dataset.step;
    if (!flowOf(b).includes(n) || n >= b.step) return;
    b.step = n; b.error = ''; b.time = null;
    if (n <= 2 && !b.fromStaff) b.staffId = null;
    render();
  },
  staff: el => { Object.assign(S.b, { staffId: el.dataset.id, date: null, time: null, step: 3, error: '' }); render(); },
  day: el => { S.b.date = el.dataset.d; render(); },
  time: el => { Object.assign(S.b, { date: el.dataset.d, time: +el.dataset.t, step: 4, error: '' }); render(); },
  reset: () => { S.b = freshBook(profile()); S.tab = 'book'; render(); },
  copy: el => {
    const text = el.dataset.text;
    try { navigator.clipboard.writeText(text).then(() => toast(t('copied')), () => toast(t('copyFail'))); } catch (e) { toast(t('copyFail')); }
  },
  resched: el => {
    const item = S.mine.items.find(x => x.token === el.dataset.tok); if (!item || !item.view) return;
    const v = item.view;
    S.b = freshBook({ resched: v.token, reschedFrom: { date: v.date, start: v.start }, services: v.services.map(s => s.id), staffId: v.staff.id, fromStaff: v.staff.id, step: 3 });
    S.tab = 'book'; render();
  },
  cancel: el => { S.confirm = el.dataset.tok; render(); },
  'cancel-no': () => { S.confirm = null; render(); },
  'cancel-yes': async el => {
    const tok = el.dataset.tok; S.confirm = null;
    try { const v = await api(`/api/public/bookings/${encodeURIComponent(tok)}/cancel`, { method: 'POST', body: {} }); replaceMine(v); toast(t('cancelledToast')); }
    catch (e) { toast(errText(e)); }
    render();
  },
};
const profile = () => { const p = local.get('rota-profile', null) || {}; return { name: p.name || '', phone: p.phone || '' }; };
function applyLang() {
  document.documentElement.lang = lang();
  const skip = $('.skip'); if (skip) skip.textContent = t('skip');
  if (S.studio) document.title = `${tr(S.studio, 'name') || S.studio.name} · ${t('book')}`;
}

document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!el || el.disabled) return;
  const fn = ACT[el.dataset.act];
  if (fn) { if (el.tagName !== 'A') e.preventDefault(); fn(el, e); }
});
document.addEventListener('change', e => {
  const t0 = e.target;
  if (t0.matches('[data-svc]')) {
    const set = new Set(S.b.services); t0.checked ? set.add(t0.value) : set.delete(t0.value);
    S.b.services = S.studio.services.map(s => s.id).filter(id => set.has(id)); S.b.error = ''; render();
  }
});
document.addEventListener('input', e => {
  const t0 = e.target; const f = t0.dataset && t0.dataset.f;
  if (!f) return;
  S.b[f] = t0.value;
  if (S.b.errors && S.b.errors[f]) { S.b.errors[f] = ''; t0.removeAttribute('aria-invalid'); const m = $('#' + t0.id + '-err'); if (m) m.textContent = ''; }
});
document.addEventListener('submit', e => {
  const form = e.target.closest('form[data-form="book"]'); if (!form) return;
  e.preventDefault();
  if (!S.b.busy) submitBooking();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape' && S.confirm) { S.confirm = null; render(); } });
setInterval(() => { if (S.studio && S.tab !== 'book') schedule(); }, 60000);

// ---------------------------------------------------------------- start
(async () => {
  const m = location.pathname.match(/^\/b\/([^/]+)$/);
  if (m) { const tok = decodeURIComponent(m[1]); addToken(tok); S.tab = 'mine'; S.highlight = tok; }
  Object.assign(S.b, profile());
  render();
  try {
    S.studio = await api('/api/public/studio');
    S.loadedAt = Date.now();
    if (!S.lang) S.lang = LANGS.includes(S.studio.defaultLang) ? S.studio.defaultLang : 'hy';
  } catch (e) { S.fatal = e; }
  applyLang();
  render();
  if (S.studio && !S.studio.paused) loadMine();
})();
})();
