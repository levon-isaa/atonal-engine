/* ============================ LIBRARY ============================
   Point at your music folder; every track in it becomes a row; click one to
   render it. No account, no keys, no setup.

   WHY THE AUDIO ALWAYS COMES FROM A FILE. There is no version of this that
   streams from Spotify or Apple Music instead. Both deliver audio through a
   protected pipeline specifically so that nothing can tap the samples, which
   is exactly what analyze.py needs -- and Spotify additionally closed
   /audio-features, /audio-analysis and preview_url to new applications on
   27 Nov 2024, so their pre-computed analysis is not available either. A
   "connect your account" button could only ever have listed titles; the audio
   would still have had to come from disk. Reading the folder does the same job
   with none of the apparatus.

   IT TOUCHES THE ENGINE AT ONE POINT, loadFile(), which is why this lives
   beside viewer.html rather than in it. It injects its own panel next to
   Credits and owns its own DOM.  */
(function(){
'use strict';
const $ = id => document.getElementById(id);
const AUDIO = /\.(mp3|m4a|aac|wav|flac|ogg|opus|aif|aiff|wma)$/i;
let FILES = [];       // {file, artist, title, secs}
let FILTER = '';

/* ---------- names ---------- */
/* "Unknown Artist" and "Unknown Album" are what Music.app writes when it has
   nothing, and a real media folder is full of them -- the one this was built
   against was every file. Falling back to the parent folder then puts the same
   meaningless word on every row, which is worse than an empty one because it
   looks like data. Any "Unknown ..." is treated as absent. */
const JUNK=/^(unknown.*|various|artists?|albums?|music|media|medialocalized|itunes|library|downloads?|compilations?)$/i;
function nameFrom(f){
  const parts=(f.webkitRelativePath||f.name).split('/');
  const base=parts[parts.length-1].replace(/\.[^.]+$/,'');
  const folder=parts.length>1?parts[parts.length-2]:'';
  return { title: base.replace(/^\d+[\s._-]+/,'').trim() || base,
           folder: JUNK.test(folder.trim()) ? '' : folder };
}

/* ---------- tags ----------
   ID3v2 (mp3) and the MP4 ilst atoms (m4a), which between them cover a normal
   library. Only the first 256KB is read: tags live at the front of both
   formats, and reading whole files here would mean pulling the entire library
   into memory just to draw a list. */
async function tags(f){
  const buf = new Uint8Array(await f.slice(0, 262144).arrayBuffer());
  const ascii=(o,n)=>String.fromCharCode.apply(null, buf.subarray(o,o+n));
  let title='', artist='';
  if(ascii(0,3)==='ID3'){
    const v=buf[3];
    const sz=(buf[6]<<21)|(buf[7]<<14)|(buf[8]<<7)|buf[9];        // syncsafe
    let o=10;
    while(o+10 <= Math.min(10+sz, buf.length)){
      const id=ascii(o,4); if(!/^[A-Z0-9]{4}$/.test(id)) break;
      const fs = v>=4 ? ((buf[o+4]<<21)|(buf[o+5]<<14)|(buf[o+6]<<7)|buf[o+7])   // syncsafe in 2.4
                      : ((buf[o+4]<<24)|(buf[o+5]<<16)|(buf[o+6]<<8)|buf[o+7]);
      if(fs<=0||o+10+fs>buf.length) break;
      if(id==='TIT2'||id==='TPE1'){
        const t=decodeText(buf.subarray(o+10, o+10+fs));
        if(id==='TIT2') title=t; else artist=t;
      }
      o += 10+fs;
      if(title&&artist) break;
    }
  }
  if(!title||!artist){
    // MP4: find the ilst text atoms directly. Walking moov/udta/meta/ilst
    // properly means handling meta's version bytes and several optional
    // containers; the markers are unambiguous enough to scan for.
    for(let i=0;i+16<buf.length;i++){
      if(buf[i]!==0xA9) continue;
      const tag=ascii(i,4);
      if(tag!=='\xA9nam' && tag!=='\xA9ART') continue;
      const dsz=(buf[i+4]<<24)|(buf[i+5]<<16)|(buf[i+6]<<8)|buf[i+7];
      if(ascii(i+8,4)!=='data' || dsz<16 || i+4+dsz>buf.length) continue;
      const t=new TextDecoder('utf-8').decode(buf.subarray(i+20, i+4+dsz)).replace(/\0+$/,'');
      if(tag==='\xA9nam'){ if(!title) title=t; } else if(!artist) artist=t;
      if(title&&artist) break;
    }
  }
  return { artist:(artist||'').trim(), title:(title||'').trim() };
}
function decodeText(b){
  const enc=b[0], body=b.subarray(1);
  try{
    if(enc===1||enc===2) return new TextDecoder('utf-16').decode(body).replace(/\0+$/,'');
    if(enc===3)          return new TextDecoder('utf-8').decode(body).replace(/\0+$/,'');
    return new TextDecoder('iso-8859-1').decode(body).replace(/\0+$/,'');
  }catch(e){ return ''; }
}

/* ---------- indexing ---------- */
async function useFiles(list){
  note('Reading tags… 0/'+list.length);
  FILES=[];
  for(let i=0;i<list.length;i++){
    const f=list[i];
    let t={artist:'',title:''};
    try{ t=await tags(f); }catch(e){}
    const nm=nameFrom(f);
    let artist=t.artist||nm.folder||'';
    if(JUNK.test(artist.trim())) artist='';
    FILES.push({ file:f, artist, title:t.title||nm.title, secs:null });
    if((i&31)===0) note('Reading tags… '+i+'/'+list.length);
  }
  FILES.sort((a,b)=>(a.artist||'~').localeCompare(b.artist||'~')||a.title.localeCompare(b.title));
  note(FILES.length+' tracks. Click one to render it.');
  render();
  lengths();
}

/* Durations are read from the container header rather than by decoding: a full
   decode of every file would pull the library into memory to populate a list.
   Capped and cancellable so a large folder cannot leave a probe running over
   thousands of files. */
function probe(file){
  return new Promise(res=>{
    const a=document.createElement('audio'), u=URL.createObjectURL(file);
    let done=false;
    const fin=v=>{ if(done) return; done=true; URL.revokeObjectURL(u); a.removeAttribute('src'); res(v); };
    a.preload='metadata';
    a.onloadedmetadata=()=>fin(isFinite(a.duration)?a.duration:null);
    a.onerror=()=>fin(null);
    setTimeout(()=>fin(null), 5000);          // a format the browser will not open
    a.src=u;
  });
}
let lenRun=0;
async function lengths(){
  const run=++lenRun, todo=FILES.slice(0,500).filter(f=>f.secs===null);
  let i=0, done=0;
  const worker=async()=>{
    while(i<todo.length && run===lenRun){
      const f=todo[i++];
      f.secs=await probe(f.file);
      if(++done%25===0 && run===lenRun) render();
    }
  };
  await Promise.all([worker(),worker(),worker(),worker()]);
  if(run===lenRun) render();
}

/* ---------- a dropped folder ----------
   The shortest path there is: no dialog, no button. dataTransfer.files
   flattens a directory to nothing, so the entries have to be walked --
   webkitGetAsEntry is the only way to see a folder at all, and readEntries
   returns at most 100 per call, which is the bug that silently truncates every
   naive implementation of this. */
async function walk(entry, out, depth){
  if(!entry || depth>6) return;
  if(entry.isFile){
    const f=await new Promise(r=>entry.file(r,()=>r(null)));
    if(f && AUDIO.test(f.name)){
      try{ Object.defineProperty(f,'webkitRelativePath',{value:entry.fullPath.replace(/^\//,'')}); }catch(e){}
      out.push(f);
    }
    return;
  }
  if(!entry.isDirectory) return;
  const rd=entry.createReader();
  for(;;){
    const batch=await new Promise(r=>rd.readEntries(r,()=>r([])));
    if(!batch.length) break;
    for(const e of batch) await walk(e, out, depth+1);
  }
}
async function onDrop(ev){
  const items=[...(ev.dataTransfer&&ev.dataTransfer.items||[])];
  const roots=items.map(it=>it.webkitGetAsEntry&&it.webkitGetAsEntry()).filter(Boolean);
  if(!roots.some(e=>e.isDirectory)) return;    // a single file: the viewer already handles it
  ev.preventDefault(); ev.stopPropagation();
  const g=$('gLibrary'); if(g) g.open=true;
  note('Reading folder…');
  const out=[];
  for(const r of roots) await walk(r, out, 0);
  if(!out.length){ note('No audio files in there.', true); return; }
  await useFiles(out);
}

/* ---------- ui ---------- */
const mmss=x=>(x==null||!isFinite(x))?'':(Math.floor(x/60)+':'+String(Math.round(x%60)).padStart(2,'0'));
function note(msg, bad){
  const el=$('libNote'); if(!el) return;
  el.textContent=msg||''; el.className='libNote'+(bad?' bad':'');
}
function render(){
  const list=$('libList'); if(!list) return;
  if(!FILES.length){
    list.innerHTML='<div class="libEmpty">Choose your music folder, or drop one anywhere on the window — every track in it becomes a row.</div>';
    $('libCount').textContent='—'; return;
  }
  const frag=document.createDocumentFragment();
  let shown=0;
  for(const f of FILES){
    if(FILTER && !((f.artist+' '+f.title).toLowerCase().includes(FILTER))) continue;
    const r=document.createElement('div');
    r.className='libRow';
    r.innerHTML='<span class="libT"></span><span class="libA"></span><span class="libS"></span>';
    r.children[0].textContent=f.title;
    r.children[1].textContent=f.artist;
    r.children[2].textContent=mmss(f.secs);
    r.title='Render '+f.title+'\n'+(f.file.webkitRelativePath||f.file.name);
    r.onclick=()=>{
      if(typeof loadFile!=='function'){ note('viewer not ready', true); return; }
      note('Loading '+f.title+'…'); loadFile(f.file);
    };
    frag.appendChild(r); shown++;
  }
  $('libCount').textContent = FILTER ? (shown+' of '+FILES.length) : String(FILES.length);
  list.innerHTML='';
  if(!shown){ list.innerHTML='<div class="libEmpty">Nothing matches “'+FILTER+'”.</div>'; return; }
  list.appendChild(frag);
}

function panel(){
  const host=$('gCredits');
  if(!host||$('gLibrary')) return;
  const css=document.createElement('style');
  css.textContent=`
  #gLibrary .libNote{opacity:.72;font-size:11px;margin:4px 0 2px}
  #gLibrary .libNote.bad{color:#ff8080;opacity:1}
  #gLibrary .libList{max-height:260px;overflow-y:auto;overflow-x:hidden;margin-top:6px;
    border-top:1px solid rgba(255,255,255,.08)}
  #gLibrary .libRow{display:grid;grid-template-columns:1fr auto;gap:0 8px;padding:4px 2px;
    border-bottom:1px solid rgba(255,255,255,.05);cursor:pointer;font-size:11px;line-height:1.25}
  #gLibrary .libRow:hover{background:rgba(255,255,255,.06)}
  #gLibrary .libT{grid-column:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #gLibrary .libA{grid-column:1;opacity:.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #gLibrary .libS{grid-column:2;grid-row:1/3;align-self:center;opacity:.55;font-variant-numeric:tabular-nums}
  #gLibrary .libEmpty{opacity:.5;font-size:11px;padding:8px 2px;line-height:1.45}
  #gLibrary input[type=file]{display:none}
  /* The shared .row puts label and .val side by side, which is right for a
     slider and wrong for a two-word button -- it overlapped at the panel's
     width. These lay their control out on its own line. */
  #gLibrary .libWide{display:block}
  #gLibrary .libWide>label{display:block;margin-bottom:3px}
  #gLibrary .libWide>.val{display:block;width:100%;text-align:left}
  #gLibrary #libFilter{width:100%;font:inherit;font-size:11px;line-height:1.35;
    background:rgba(0,0,0,.25);color:inherit;border:1px solid rgba(255,255,255,.12);
    border-radius:4px;padding:4px 6px}`;
  document.head.appendChild(css);
  const d=document.createElement('details');
  d.id='gLibrary';
  d.innerHTML =
    '<summary>Library</summary>'+
    '<div class="row libWide"><label></label><span class="val">'+
      '<label class="btn" for="libFolder" title="Your music folder. It is read in the browser — nothing is uploaded until you pick a track.">Choose music folder</label>'+
      '<input id="libFolder" type="file" webkitdirectory directory multiple></span></div>'+
    '<div class="row libWide"><label></label><span class="val">'+
      '<input type="text" id="libFilter" placeholder="Filter…" autocomplete="off" spellcheck="false"></span></div>'+
    '<div class="row"><label>Tracks</label><span class="val" id="libCount">&mdash;</span></div>'+
    '<div class="libNote" id="libNote"></div>'+
    '<div class="libList" id="libList"></div>';
  host.parentNode.insertBefore(d, host);
  $('libFolder').addEventListener('change',async ev=>{
    const list=Array.from(ev.target.files||[]).filter(f=>AUDIO.test(f.name));
    ev.target.value='';
    if(!list.length){ note('No audio files in that folder.', true); return; }
    await useFiles(list);
  });
  const flt=$('libFilter');
  flt.addEventListener('input',()=>{ FILTER=flt.value.trim().toLowerCase(); render(); });
  // Capture phase: the viewer has its own window-level drop handler for a
  // single audio file, and a dropped FOLDER has to be claimed before it.
  addEventListener('dragover',e=>e.preventDefault());
  addEventListener('drop',e=>{ onDrop(e); }, true);
  render();
}

if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',panel);
else panel();

window.__LIB = { get files(){return FILES;}, useFiles, probe, render };
})();
