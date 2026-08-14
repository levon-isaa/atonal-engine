/**
 * Control primitives — plain DOM, no framework.
 *
 * Every control writes to the store and nothing else. None of them read back from the renderer,
 * and none of them hold their own copy of the value, so there is no second source of truth to
 * drift out of sync.
 *
 * `input` fires continuously during a drag. That is wanted for material and light parameters,
 * which are uniform writes. It is NOT wanted for geometry, where each event rebuilds an
 * ExtrudeGeometry — so those controls declare `commit:true` and fire on `change` instead, at the
 * end of the gesture, with a live numeric readout in between.
 */

export function el(tag, cls, txt) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
}

export function section(title, collapsed = false) {
  const wrap = el('div', 'sec');
  const head = el('button', 'sec-h');
  head.innerHTML = `<span>${title}</span><i>${collapsed ? '+' : '−'}</i>`;
  const body = el('div', 'sec-b');
  if (collapsed) body.style.display = 'none';
  head.onclick = () => {
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : '';
    head.querySelector('i').textContent = open ? '+' : '−';
  };
  wrap.append(head, body);
  return { wrap, body };
}

export function slider(parent, { label, min, max, step, value, commit = false, onChange }) {
  const row = el('div', 'row');
  const lab = el('label', null, label);
  const out = el('span', 'val', (+value).toFixed(step < 0.01 ? 3 : step < 1 ? 2 : 0));
  const inp = el('input');
  inp.type = 'range'; inp.min = min; inp.max = max; inp.step = step; inp.value = value;
  const show = () => { out.textContent = (+inp.value).toFixed(step < 0.01 ? 3 : step < 1 ? 2 : 0); };
  inp.addEventListener('input', () => { show(); if (!commit) onChange(+inp.value); });
  if (commit) inp.addEventListener('change', () => onChange(+inp.value));
  row.append(lab, inp, out);
  parent.append(row);
  return { row, input: inp, set: (v) => { inp.value = v; show(); } };
}

export function select(parent, { label, options, value, onChange }) {
  const row = el('div', 'row');
  const lab = el('label', null, label);
  const sel = el('select');
  for (const o of options) {
    const opt = el('option', null, o.label ?? o);
    opt.value = o.value ?? o;
    sel.append(opt);
  }
  sel.value = value;
  sel.addEventListener('change', () => onChange(sel.value));
  row.append(lab, sel);
  parent.append(row);
  return { row, select: sel, set: (v) => { sel.value = v; } };
}

export function color(parent, { label, value, onChange }) {
  const row = el('div', 'row');
  const lab = el('label', null, label);
  const inp = el('input'); inp.type = 'color'; inp.value = value;
  inp.addEventListener('input', () => onChange(inp.value));
  row.append(lab, inp);
  parent.append(row);
  return { row, input: inp, set: (v) => { inp.value = v; } };
}

export function toggle(parent, { label, value, onChange }) {
  const row = el('div', 'row');
  const lab = el('label', null, label);
  const btn = el('button', 'tog', value ? 'ON' : 'OFF');
  btn.classList.toggle('on', !!value);
  btn.onclick = () => {
    const next = !btn.classList.contains('on');
    btn.classList.toggle('on', next);
    btn.textContent = next ? 'ON' : 'OFF';
    onChange(next);
  };
  row.append(lab, btn);
  parent.append(row);
  return { row, button: btn };
}

export function buttons(parent, { label, items }) {
  const row = el('div', 'row wrap');
  if (label) row.append(el('label', null, label));
  const box = el('div', 'btns');
  for (const it of items) {
    const b = el('button', 'mini', it.label);
    b.onclick = it.onClick;
    box.append(b);
  }
  row.append(box);
  parent.append(row);
  return row;
}
