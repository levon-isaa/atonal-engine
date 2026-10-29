// Rota admin: every company that signed up, how far they got, and their pilot.
(() => {
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ESC[c]);
const S = { me: null, studios: [], today: '', filter: 'all', q: '', error: '', loading: true };
const KINDS = { hair: 'Hair salon', barber: 'Barbershop', nails: 'Nails', brows: 'Brows & lashes', skin: 'Skin & beauty', massage: 'Massage', other: 'Other' };

async function api(path, opts = {}) {
  const res = await fetch(path, { method: opts.method || 'GET', credentials: 'same-origin',
    headers: opts.method && opts.method !== 'GET' ? { 'X-Rota': '1', 'Content-Type': 'application/json' } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  let data = null; try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) { const err = new Error((data && data.message) || res.statusText); err.status = res.status; throw err; }
  return data;
}
function toast(msg) {
  const box = $('#toasts'); const el = document.createElement('div'); el.className = 'toast'; el.textContent = msg; box.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}
const day = s => s ? new Date(s.length === 10 ? s + 'T12:00:00' : s).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
const daysUntil = s => Math.round((Date.parse(s + 'T12:00:00') - Date.parse(S.today + 'T12:00:00')) / 864e5);
function ago(s) {
  if (!s) return 'never';
  const m = Math.round((Date.now() - Date.parse(s)) / 60000);
  return m < 60 ? 'within the hour' : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`;
}
const n = (k, w) => `${k} ${w}${k === 1 ? '' : 's'}`;
const real = () => S.studios.filter(s => s.status !== 'demo');
const ending = s => s.status === 'pilot' && s.pilot_ends && daysUntil(s.pilot_ends) <= 7;

function view() {
  if (S.loading) return '<div class="loading">Loading…</div>';
  if (!S.me) return `<form class="card signin" id="signin" novalidate><div class="logo"><span class="mark" aria-hidden="true">R</span>Rota admin</div>
    <h1>Sign in</h1>
    <div class="field"><label for="ad-email">Email</label><input id="ad-email" name="email" type="email" autocomplete="username" required></div>
    <div class="field"><label for="ad-pw">Password</label><input id="ad-pw" name="password" type="password" autocomplete="current-password" required></div>
    <p class="form-err" id="ad-err" role="alert">${esc(S.error)}</p>
    <button class="btn primary">Sign in</button>
    <p class="muted" style="margin:0;font-size:14px">Accounts are made on the server: <code>python manage.py create-admin you@example.com</code></p></form>`;
  const all = real();
  const counts = { all: all.length, pilot: all.filter(s => s.status === 'pilot').length, active: all.filter(s => s.status === 'active').length,
    paused: all.filter(s => s.status === 'paused').length, ending: all.filter(ending).length };
  const week = all.reduce((n, s) => n + (s.bookings7 || 0), 0), online = all.reduce((n, s) => n + (s.online7 || 0), 0);
  const q = S.q.trim().toLowerCase();
  const shown = all.filter(s => (S.filter === 'all' || (S.filter === 'ending' ? ending(s) : s.status === S.filter)) &&
    (!q || [s.name, s.slug, s.owner_name, s.owner_email, s.owner_phone].join(' ').toLowerCase().includes(q)));
  const filters = [['all', 'All'], ['pilot', 'In pilot'], ['active', 'Paying'], ['paused', 'Paused'], ['ending', 'Pilot ends within 7 days']];
  return `<div class="wrap"><header class="top"><div class="logo"><span class="mark" aria-hidden="true">R</span>Rota admin</div>
      <div class="me"><span>${esc(S.me.email)}</span><button type="button" class="btn sm ghost" id="logout">Sign out</button></div></header>
    <main>
      <h1 class="sr">Studios</h1>
      <div class="stats">
        <div class="card stat"><b>${counts.all}</b><span>Studios</span></div>
        <div class="card stat"><b>${counts.pilot}</b><span>In pilot</span></div>
        <div class="card stat"><b>${counts.active}</b><span>Paying</span></div>
        <div class="card stat"><b>${counts.ending}</b><span>Pilot ends within 7 days</span></div>
        <div class="card stat"><b>${week}</b><span>Bookings made this week</span></div>
        <div class="card stat"><b>${week ? Math.round(online / week * 100) : 0}%</b><span>Of them made online</span></div>
      </div>
      <div class="bar"><div class="filters" role="group" aria-label="Show">${filters.map(([k, l]) => `<button type="button" class="btn sm" data-filter="${k}" aria-pressed="${S.filter === k}">${l} <span class="num">${counts[k]}</span></button>`).join('')}</div>
        <label class="sr" for="q">Search studios</label><input type="search" id="q" placeholder="Search name, address, owner" value="${esc(S.q)}"></div>
      <div class="list">${shown.length ? shown.map(card).join('') : '<div class="card empty"><h2>No studios here yet</h2><p>New sign-ups appear at the top.</p></div>'}</div>
    </main></div>`;
}
function card(s) {
  const wa = String(s.owner_phone || '').replace(/\D/g, '');
  const left = s.pilot_ends ? daysUntil(s.pilot_ends) : null;
  const pilot = s.status !== 'pilot' || left === null ? (s.pilot_ends ? day(s.pilot_ends) : '–')
    : left < 0 ? `<span class="bad-txt">Ended ${day(s.pilot_ends)}</span>` : left <= 7 ? `<span class="warn-txt">${day(s.pilot_ends)} (in ${left} days)</span>` : `${day(s.pilot_ends)} (in ${left} days)`;
  const pill = { pilot: 'accent', active: 'ok', paused: 'warn' }[s.status] || '';
  return `<article class="card studio" data-slug="${esc(s.slug)}">
    <div><h2>${esc(s.name)} <span class="pill ${pill}">${s.status === 'active' ? 'Paying' : s.status === 'pilot' ? 'Pilot' : 'Paused'}</span></h2>
      <div class="sub"><a href="${esc(s.url)}/" target="_blank" rel="noopener">${esc(s.url.replace(/^https?:\/\//, ''))}</a> · ${esc(KINDS[s.kind] || s.kind)} · signed up ${esc(day(s.created))}</div>
      <div class="acts" style="margin-top:10px">
        <label class="sr" for="st-${esc(s.slug)}">Status of ${esc(s.name)}</label>
        <select id="st-${esc(s.slug)}" data-act="status"><option value="pilot"${s.status === 'pilot' ? ' selected' : ''}>Pilot</option><option value="active"${s.status === 'active' ? ' selected' : ''}>Paying</option><option value="paused"${s.status === 'paused' ? ' selected' : ''}>Paused</option></select>
        <button type="button" class="btn sm" data-act="extend">Extend pilot 30 days</button>
        <a class="btn sm ghost" href="${esc(s.url)}/desk/" target="_blank" rel="noopener">Desk</a>
      </div></div>
    <dl><dt>Owner</dt><dd>${esc(s.owner_name) || '–'}</dd>
      <dt>Email</dt><dd>${s.owner_email ? `<a href="mailto:${esc(s.owner_email)}">${esc(s.owner_email)}</a>` : '–'}</dd>
      <dt>Phone</dt><dd>${wa ? `<a href="https://wa.me/${wa}" target="_blank" rel="noopener">${esc(s.owner_phone)}</a>` : esc(s.owner_phone) || '–'}</dd>
      <dt>Pilot ends</dt><dd>${pilot}</dd></dl>
    <dl>${s.problem ? `<dt>Problem</dt><dd class="bad-txt">${esc(s.problem)}</dd>` : `
      <dt>Set up</dt><dd>${n(s.staff, 'specialist')}, ${n(s.services, 'service')}</dd>
      <dt>Clients</dt><dd>${s.clients}</dd>
      <dt>This week</dt><dd>${n(s.bookings7, 'booking')}, ${s.online7} online</dd>
      <dt>Desk used</dt><dd>${esc(ago(s.lastSeen))}</dd>`}</dl>
    <div class="notes"><label for="nt-${esc(s.slug)}">Notes (only you see these)</label><textarea id="nt-${esc(s.slug)}" rows="2" data-act="notes" placeholder="Visited on…, prefers WhatsApp, wants…">${esc(s.notes)}</textarea></div>
  </article>`;
}
function render() {
  const ae = document.activeElement; const id = ae && ae.id; const sel = id === 'q' ? [ae.selectionStart, ae.selectionEnd] : null;
  $('#root').innerHTML = view();
  if (id) { const el = document.getElementById(id); if (el) { el.focus(); if (sel) el.setSelectionRange(sel[0], sel[1]); } }
}
async function load() {
  try { const d = await api('/api/admin/studios'); S.studios = d.studios; S.today = d.today; }
  catch (e) { if (e.status === 401) S.me = null; }
  render();
}
async function update(slug, body, msg) {
  try { const r = await api(`/api/admin/studios/${slug}`, { method: 'POST', body }); Object.assign(S.studios.find(s => s.slug === slug), { status: r.status, pilot_ends: r.pilot_ends, notes: r.notes }); toast(msg); render(); }
  catch (e) { toast(e.message); }
}

document.addEventListener('submit', async e => {
  if (e.target.id !== 'signin') return;
  e.preventDefault();
  const f = e.target.elements;
  try { S.me = await api('/api/admin/login', { method: 'POST', body: { email: f.email.value.trim(), password: f.password.value } }); S.error = ''; await load(); }
  catch (err) { S.error = err.status === 429 ? 'Too many tries. Wait 15 minutes.' : err.status === 401 ? "That email and password don't match." : "Can't reach the server."; render(); $('#ad-pw').focus(); }
});
document.addEventListener('click', async e => {
  const f = e.target.closest('[data-filter]'); if (f) { S.filter = f.dataset.filter; render(); return; }
  if (e.target.closest('#logout')) { try { await api('/api/admin/logout', { method: 'POST', body: {} }); } catch (err) { /* signed out anyway */ } S.me = null; render(); return; }
  const b = e.target.closest('[data-act="extend"]');
  if (b) {
    const slug = b.closest('[data-slug]').dataset.slug; const s = S.studios.find(x => x.slug === slug);
    const from = s.pilot_ends && s.pilot_ends > S.today ? s.pilot_ends : S.today;
    const d = new Date(from + 'T12:00:00'); d.setDate(d.getDate() + 30);
    await update(slug, { pilotEnds: d.toISOString().slice(0, 10), status: 'pilot' }, `${s.name}: pilot now ends ${day(d.toISOString().slice(0, 10))}`);
  }
});
document.addEventListener('change', e => {
  const el = e.target.closest('[data-act="status"]'); if (!el) return;
  const slug = el.closest('[data-slug]').dataset.slug;
  update(slug, { status: el.value }, `Status saved`);
});
document.addEventListener('focusout', e => {
  const el = e.target.closest('[data-act="notes"]'); if (!el) return;
  const slug = el.closest('[data-slug]').dataset.slug; const s = S.studios.find(x => x.slug === slug);
  if (s && el.value !== (s.notes || '')) update(slug, { notes: el.value }, 'Notes saved');
});
document.addEventListener('input', e => { if (e.target.id === 'q') { S.q = e.target.value; render(); } });

(async () => {
  try { S.me = await api('/api/admin/me'); } catch (e) { S.me = null; }
  S.loading = false;
  if (S.me) await load(); else render();
  setInterval(() => { if (S.me && !document.hidden && !document.activeElement.closest('textarea')) load(); }, 60000);
})();
})();
