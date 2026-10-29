#!/usr/bin/env node
// Bakes the Armenian text from web/site/i18n.js into the sales site's HTML, so the pages read well
// before their script runs and link previews (WhatsApp, Facebook) show real words.
// Run after editing the dictionary:   node tools/site_text.js        (--check: fail if out of date)
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const ctx = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(root, 'web/site/i18n.js'), 'utf8'), ctx);
const D = ctx.window.ROTA_SITE;
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const text = k => {
  for (const l of ['hy', 'ru', 'en']) if (!(k in D[l])) throw new Error(`"${k}" is missing from the ${l} dictionary`);
  return D.hy[k].replace(/\{domain\}/g, 'rota.am');
};

let stale = false;
for (const page of ['index.html', 'start.html']) {
  const file = path.join(root, 'web/site', page);
  const before = fs.readFileSync(file, 'utf8');
  let html = before.replace(/(<([a-z0-9]+)\b[^>]*\sdata-t="([a-z0-9_]+)"[^>]*>)[^<]*(<\/\2>)/g, (m, open, tag, key, close) => open + esc(text(key)) + close);
  html = html.replace(/<[a-z0-9]+\b[^>]*\sdata-t-attr="([^"]+)"[^>]*>/g, tag => {
    for (const pair of tag.match(/data-t-attr="([^"]+)"/)[1].split(',')) {
      const [attr, key] = pair.split(':');
      tag = tag.replace(new RegExp(`\\s${attr}="[^"]*"`), ` ${attr}="${esc(text(key))}"`);
    }
    return tag;
  });
  if (html !== before) {
    stale = true;
    if (!process.argv.includes('--check')) fs.writeFileSync(file, html);
  }
}
if (process.argv.includes('--check') && stale) { console.error('web/site HTML is out of date: run node tools/site_text.js'); process.exit(1); }
console.log(stale ? 'updated the Armenian text in web/site' : 'web/site HTML is up to date');
