// Rota sales site: language switching for the homepage and the sign-up page, and the sign-up form.
(() => {
'use strict';
const D = window.ROTA_SITE;
const LANGS = ['hy', 'ru', 'en'];
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };

// Studios live at <address>.<this host>; www. is the same site.
const HOST = location.host.replace(/^www\./, '');
const studioUrl = slug => `${location.protocol}//${slug}.${HOST}`;
let info = { demo: null, contactPhone: '', contactEmail: '' };

function pickLang() {
  const q = new URLSearchParams(location.search).get('lang');
  if (LANGS.includes(q)) return q;
  const saved = store.get('rota-site-lang');
  if (LANGS.includes(saved)) return saved;
  for (const l of navigator.languages || []) { const k = String(l).slice(0, 2).toLowerCase(); if (LANGS.includes(k)) return k; }
  return 'hy';
}
let lang = pickLang();
const t = (k, vars = {}) => String((D[lang] && D[lang][k]) ?? D.en[k] ?? k).replace(/\{(\w+)\}/g, (_, v) => vars[v] ?? (v === 'domain' ? HOST : `{${v}}`));

function apply() {
  document.documentElement.lang = lang;
  if (document.body.dataset.title) document.title = t(document.body.dataset.title);
  for (const el of $$('[data-t]')) el.textContent = t(el.dataset.t);
  for (const el of $$('[data-t-attr]')) {
    for (const pair of el.dataset.tAttr.split(',')) { const [attr, key] = pair.split(':'); el.setAttribute(attr, t(key)); }
  }
  for (const b of $$('[data-lang]')) b.setAttribute('aria-pressed', String(b.dataset.lang === lang));
  for (const img of $$('img[data-shot]')) img.src = `/assets/site/img/${img.dataset.shot.replace('{lang}', lang)}`;
  const sel = $('#su-lang'); if (sel && !sel.dataset.touched) sel.value = lang;
  for (const el of $$('[data-domain]')) el.textContent = `.${HOST}`;
  if (window.ROTA_SITE_APPLIED) window.ROTA_SITE_APPLIED();
}

document.addEventListener('click', e => {
  const b = e.target.closest('[data-lang]');
  if (!b) return;
  lang = b.dataset.lang; store.set('rota-site-lang', lang); apply();
});

async function api(path, opts = {}) {
  const res = await fetch(path, { method: opts.method || 'GET', headers: opts.body ? { 'Content-Type': 'application/json', 'X-Rota': '1' } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined, credentials: 'same-origin' });
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) { const err = new Error((data && data.message) || res.statusText); err.status = res.status; err.code = data && data.error; err.field = data && data.field; throw err; }
  return data;
}

// ---------------------------------------------------------------- homepage
function homepage() {
  for (const a of $$('[data-demo]')) {
    if (!info.demo) { a.hidden = true; continue; }
    a.hidden = false; a.href = info.demo + (a.dataset.demo === 'desk' ? '/desk/' : '/');
  }
  const wa = String(info.contactPhone || '').replace(/\D/g, '');
  const box = $('#contact');
  if (box) {
    box.hidden = !wa && !info.contactEmail;
    const w = $('#contact-wa'), m = $('#contact-mail');
    if (w) { w.hidden = !wa; w.href = `https://wa.me/${wa}`; $('#contact-wa-num').textContent = info.contactPhone; }
    if (m) { m.hidden = !info.contactEmail; m.href = `mailto:${info.contactEmail}`; $('#contact-mail-addr').textContent = info.contactEmail; }
  }
  const form = $('#signin-form');
  if (form) form.addEventListener('submit', e => {
    e.preventDefault();
    const input = $('#signin-slug'); const slug = input.value.trim().toLowerCase().replace(/^https?:\/\//, '').split('.')[0];
    const ok = /^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$/.test(slug);
    input.setAttribute('aria-invalid', String(!ok)); $('#signin-err').textContent = ok ? '' : t('signin_err');
    if (ok) location.assign(`${studioUrl(slug)}/desk/`); else input.focus();
  });
}

// ---------------------------------------------------------------- sign-up
const HY = { 'ա': 'a', 'բ': 'b', 'գ': 'g', 'դ': 'd', 'ե': 'e', 'զ': 'z', 'է': 'e', 'ը': 'y', 'թ': 't', 'ժ': 'zh', 'ի': 'i', 'լ': 'l', 'խ': 'kh', 'ծ': 'ts', 'կ': 'k', 'հ': 'h', 'ձ': 'dz', 'ղ': 'gh', 'ճ': 'ch', 'մ': 'm', 'յ': 'y', 'ն': 'n', 'շ': 'sh', 'ո': 'o', 'չ': 'ch', 'պ': 'p', 'ջ': 'j', 'ռ': 'r', 'ս': 's', 'վ': 'v', 'տ': 't', 'ր': 'r', 'ց': 'ts', 'ւ': 'v', 'փ': 'p', 'ք': 'k', 'և': 'ev', 'օ': 'o', 'ֆ': 'f' };
const RU = { 'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya' };
function slugify(s) {
  const low = String(s).toLowerCase().replace(/ու/g, 'u');
  const latin = Array.from(low).map(ch => HY[ch] ?? RU[ch] ?? ch).join('');
  return latin.normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 30).replace(/-+$/, '');
}

function signup() {
  const form = $('#signup'); if (!form) return;
  const F = n => form.elements.namedItem(n);
  const slugIn = F('slug'), status = $('#slug-status');
  let slugTouched = false, checkTimer = 0, checkSeq = 0, slugState = { slug: '', ok: null, code: '' };

  const fieldErr = (name, msg) => {
    const el = F(name); const out = $(`#err-${name}`);
    if (el && el.setAttribute) el.setAttribute('aria-invalid', msg ? 'true' : 'false');
    if (out) out.textContent = msg || '';
  };
  const showSlug = () => {
    status.className = 'slug-status';
    if (!slugIn.value.trim()) { status.textContent = ''; return; }
    if (slugState.ok === null) { status.textContent = t('st_checking'); return; }
    if (slugState.ok) { status.textContent = t('st_slug_free', { url: `${slugState.slug}.${HOST}` }); status.classList.add('ok'); return; }
    status.textContent = t('e_' + slugState.code) || ''; status.classList.add('bad');
  };
  const check = () => {
    clearTimeout(checkTimer);
    const v = slugIn.value.trim().toLowerCase();
    slugState = { slug: v, ok: null, code: '' }; fieldErr('slug', ''); showSlug();
    if (!v) return;
    const seq = ++checkSeq;
    checkTimer = setTimeout(async () => {
      try {
        const r = await api(`/api/site/slug?s=${encodeURIComponent(v)}`);
        if (seq !== checkSeq) return;
        slugState = { slug: r.slug || v, ok: !!r.ok, code: r.error || '' };
      } catch (e) { if (seq === checkSeq) slugState = { slug: v, ok: null, code: '' }; }
      showSlug();
    }, 350);
  };
  F('business').addEventListener('input', () => { if (!slugTouched) { slugIn.value = slugify(F('business').value); check(); } });
  slugIn.addEventListener('input', () => { slugTouched = slugIn.value.trim() !== ''; slugIn.value = slugIn.value.toLowerCase().replace(/[^a-z0-9-]/g, ''); check(); });
  F('lang').addEventListener('change', () => { F('lang').dataset.touched = '1'; });
  $('#pw-toggle').addEventListener('click', () => {
    const pw = F('password'); const show = pw.type === 'password';
    pw.type = show ? 'text' : 'password';
    $('#pw-toggle').dataset.t = show ? 'st_hide' : 'st_show'; $('#pw-toggle').textContent = t($('#pw-toggle').dataset.t);
    $('#pw-toggle').setAttribute('aria-pressed', String(show));
  });
  window.ROTA_SITE_APPLIED = showSlug;

  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (form.dataset.busy) return;
    const body = Object.fromEntries(['business', 'slug', 'name', 'email', 'phone', 'password', 'lang', 'website'].map(n => [n, (F(n).value || '').trim()]));
    body.password = F('password').value;
    body.kind = (form.querySelector('input[name="kind"]:checked') || {}).value || 'other';
    // The quick checks people need before the round trip; the server checks everything again.
    const order = [['business', !body.business && 'missing_business'], ['slug', !body.slug && 'bad_slug'], ['name', !body.name && 'missing_name'],
      ['email', !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(body.email) && (body.email ? 'bad_email' : 'missing_email')],
      ['phone', (body.phone.replace(/\D/g, '').length < 8) && (body.phone ? 'bad_phone' : 'missing_phone')],
      ['password', body.password.length < 10 && 'short_password']];
    let firstBad = null;
    for (const [name, code] of order) { fieldErr(name, code ? t('e_' + code) : ''); if (code && !firstBad) firstBad = name; }
    $('#form-err').textContent = '';
    if (firstBad) { F(firstBad).focus(); return; }
    form.dataset.busy = '1'; const btn = $('#su-go'); btn.disabled = true; btn.textContent = t('st_busy');
    try {
      const r = await api('/api/site/signup', { method: 'POST', body });
      location.assign(r.desk);
    } catch (err) {
      delete form.dataset.busy; btn.disabled = false; btn.textContent = t('st_submit');
      const msg = err.status ? (D[lang]['e_' + err.code] || D.en['e_' + err.code] || err.message || t('e_generic')) : t('e_offline');
      if (err.field && F(err.field)) { fieldErr(err.field, msg); F(err.field).focus(); }
      else { $('#form-err').textContent = msg; }
    }
  });
}

apply();
signup();
api('/api/site/info').then(d => { info = d; homepage(); }).catch(() => homepage());
})();
