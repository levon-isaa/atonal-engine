/* ============================ LIBRARY PICKER ============================
   "Play what is in my Spotify library" -- with the audio coming from the
   user's own files.

   WHY IT IS SPLIT LIKE THIS, because it is the whole design and it is not a
   compromise made out of laziness. Neither Spotify nor Apple will hand a web
   page decoded audio: playback runs through a protected pipeline precisely so
   that nothing can tap the samples, which is exactly what analyze.py needs.
   What the APIs DO allow is reading someone's library. So the library is used
   as an INDEX -- it says what you own and what you want to see -- and the
   audio comes from the copy you already have on disk. The match between the
   two is this file's actual job.

   LOOPBACK, NOT LOCALHOST. Spotify stopped accepting http redirect URIs in
   2025 with one exception: loopback IP literals, http://127.0.0.1 and
   http://[::1]. "http://localhost:8770/..." is rejected as invalid. The server
   already binds 127.0.0.1, so REDIRECT below is the form that has to be
   registered in the dashboard -- typing localhost there fails at the consent
   screen with an error that does not say why.

   PKCE, SO THERE IS NO SECRET. A client secret in a page served to a browser
   is not a secret. The authorization-code + PKCE flow exists for exactly this
   and needs only the (public) client id, so nothing here has to be kept.  */
(function(){
'use strict';
const REDIRECT = 'http://127.0.0.1:8770/callback';
const SCOPES   = 'user-library-read playlist-read-private';
const LS = { cid:'atonal.spotify.cid', tok:'atonal.spotify.tok' };
const $ = id => document.getElementById(id);
let TRACKS = [];      // {artist,title,album,key}     from the library
let FILES  = [];      // {file,label,key}             from the chosen folder
let MATCH  = new Map();// library index -> {file,score}

/* ---------- storage ---------- */
const getJSON = k => { try { return JSON.parse(localStorage.getItem(k)||'null'); } catch(e){ return null; } };
const setJSON = (k,v) => { try { localStorage.setItem(k,JSON.stringify(v)); } catch(e){} };
const clientId = () => (localStorage.getItem(LS.cid)||'').trim();

/* ---------- PKCE ---------- */
const b64url = buf => btoa(String.fromCharCode(...new Uint8Array(buf)))
  .replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
async function challenge(verifier){
  return b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
}
function verifier(){
  const a=new Uint8Array(64); crypto.getRandomValues(a);
  return b64url(a).slice(0,96);
}

async function connect(){
  const cid = clientId();
  if(!cid){ note('Paste your Spotify client id first.', true); return; }
  const v = verifier();
  sessionStorage.setItem('atonal.spotify.ver', v);
  const p = new URLSearchParams({
    client_id: cid, response_type:'code', redirect_uri: REDIRECT,
    code_challenge_method:'S256', code_challenge: await challenge(v), scope: SCOPES });
  location.href = 'https://accounts.spotify.com/authorize?' + p.toString();
}

/* The redirect comes back to /callback?code=... and the server serves the
   viewer there, so this runs on a fresh page load. The code is single-use and
   stays in the address bar until it is cleared, so a refresh would otherwise
   retry a code Spotify has already burned and fail for a reason that looks
   like a bad client id. Cleared with replaceState the moment it is read. */
async function handleCallback(){
  const q = new URLSearchParams(location.search);
  const code = q.get('code'), err = q.get('error');
  if(!code && !err) return false;
  history.replaceState({}, '', location.pathname);
  if(err){ note('Spotify declined: '+err, true); return true; }
  const v = sessionStorage.getItem('atonal.spotify.ver');
  if(!v){ note('Lost the PKCE verifier -- connect again.', true); return true; }
  const body = new URLSearchParams({ client_id: clientId(), grant_type:'authorization_code',
    code, redirect_uri: REDIRECT, code_verifier: v });
  try{
    const r = await fetch('https://accounts.spotify.com/api/token', { method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
    const j = await r.json();
    if(!r.ok) throw new Error(j.error_description||j.error||('HTTP '+r.status));
    store(j); note('Connected.');
    await loadLibrary();
  }catch(e){ note('Token exchange failed: '+e.message, true); }
  return true;
}
function store(j){
  const t = getJSON(LS.tok)||{};
  setJSON(LS.tok, { access_token:j.access_token,
    refresh_token: j.refresh_token || t.refresh_token,
    expires_at: Date.now() + (j.expires_in||3600)*1000 - 60000 });
}
async function token(){
  const t = getJSON(LS.tok);
  if(!t) return null;
  if(Date.now() < t.expires_at) return t.access_token;
  if(!t.refresh_token) return null;
  const body = new URLSearchParams({ client_id: clientId(),
    grant_type:'refresh_token', refresh_token: t.refresh_token });
  const r = await fetch('https://accounts.spotify.com/api/token', { method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
  if(!r.ok){ localStorage.removeItem(LS.tok); return null; }
  const j = await r.json(); store(j); return j.access_token;
}

/* ---------- the library ---------- */
async function api(url){
  const tk = await token();
  if(!tk) throw new Error('not connected');
  const r = await fetch(url, { headers:{ Authorization:'Bearer '+tk } });
  if(r.status===429){
    const wait = (+r.headers.get('Retry-After')||2);
    note('Spotify rate limit -- waiting '+wait+'s'); 
    await new Promise(s=>setTimeout(s, wait*1000));
    return api(url);
  }
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json();
}
async function loadLibrary(){
  note('Reading your library…');
  const out = [];
  let url = 'https://api.spotify.com/v1/me/tracks?limit=50';
  // Capped: this is a picker, not a sync. 1000 covers almost every library and
  // keeps the paging inside Spotify's rate limit without a backoff dance.
  while(url && out.length < 1000){
    const j = await api(url);
    for(const it of (j.items||[])){
      const t = it.track; if(!t) continue;
      out.push({ artist:(t.artists||[]).map(a=>a.name).join(', '),
                 title:t.name, album:(t.album||{}).name||'',
                 secs:(t.duration_ms||0)/1000 });
    }
    url = j.next;
    note('Reading your library… '+out.length);
  }
  TRACKS = out.map(t => Object.assign(t, { key: keyOf(t.artist+' '+t.title) }));
  MODE='list';
  note(TRACKS.length+' saved tracks.');
  rematch(); render();
}

/* ---------- a pasted list, which is the path that needs no credentials ----------
   WHY THIS EXISTS ALONGSIDE OAUTH. Spotify needs a registered client id before
   anyone can press Connect, and Apple Music needs a PAID developer account to
   mint a MusicKit token -- so an OAuth-only feature is one nobody can try on
   the day it ships, and Apple users could not use at all.
   Every music app can already export a list. Apple's own Music.app does it
   with File > Library > Export Playlist, and what it writes is tab-separated
   with Name, Artist and Time columns -- so an Apple library arrives here
   complete WITH durations, and the length check below works on it in full.
   A plain "Artist - Title" per line is accepted too; those have no duration,
   so they are matched on text alone and the row says so rather than implying a
   confirmation that was never made. */
function parseList(text){
  const lines=text.split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
  if(!lines.length) return [];
  const out=[];
  // Tab-separated with a header naming the columns: an exported playlist.
  const head=lines[0].split('\t');
  if(head.length>2){
    const ix=n=>head.findIndex(h=>h.trim().toLowerCase()===n);
    const iN=ix('name'), iA=ix('artist'), iT=ix('time');
    if(iN>=0 && iA>=0){
      for(const l of lines.slice(1)){
        const c=l.split('\t'); if(c.length<=Math.max(iN,iA)) continue;
        const title=(c[iN]||'').trim(), artist=(c[iA]||'').trim();
        if(!title) continue;
        out.push({ artist, title, album:'', secs: iT>=0 ? dur(c[iT]) : 0 });
      }
      return out;
    }
  }
  // Otherwise one track per line: "Artist - Title", or "Title - Artist".
  for(const l of lines){
    const m=l.split(/\s+[-\u2013\u2014]\s+/);
    if(m.length>=2) out.push({ artist:m[0].trim(), title:m.slice(1).join(' - ').trim(), album:'', secs:0 });
    else            out.push({ artist:'', title:l, album:'', secs:0 });
  }
  return out;
}
/* "3:52", "232", or milliseconds -- exports disagree and the ambiguity is real,
   so it is resolved by magnitude: nothing in a library is 1000 seconds long
   and expressed as a bare number that also happens to be its millisecond
   count, but 232000 is unmistakably ms. */
function dur(v){
  v=(v||'').trim(); if(!v) return 0;
  if(/^\d+:\d{1,2}$/.test(v)){ const [m,s]=v.split(':').map(Number); return m*60+s; }
  const n=parseFloat(v); if(!isFinite(n)||n<=0) return 0;
  return n>1000 ? n/1000 : n;
}
function useList(){
  const ta=$('libPaste'); if(!ta) return;
  const got=parseList(ta.value);
  if(!got.length){ note('Nothing parsed from that. One track per line, or paste an exported playlist.', true); return; }
  TRACKS = got.map(t=>Object.assign(t,{key:keyOf(t.artist+' '+t.title)}));
  MODE='list';
  const withDur=TRACKS.filter(t=>t.secs>0).length;
  note(TRACKS.length+' tracks'+(withDur?(' · '+withDur+' with lengths'):' · no lengths, text match only'));
  rematch(); render();
}

/* ---------- local files ---------- */
/* webkitdirectory rather than showDirectoryPicker: the picker is Chromium-only
   and its value is a handle that persists across sessions, which is worth
   having and is not what a prototype needs. This works everywhere and hands
   back real File objects, which is all loadFile() wants. */
const AUDIO = /\.(mp3|m4a|aac|wav|flac|ogg|opus|aif|aiff|wma)$/i;

/* THE FOLDER IS THE LIBRARY, and this is the path that should be the default.
   Connecting Spotify means registering a developer app, and an app starts in
   development mode capped at 25 users added by hand -- so an OAuth-first
   feature cannot be shipped to the public at all until Spotify approves a
   quota extension. Meanwhile the files are RIGHT THERE, and when there is no
   track list to match against, a file needs no matching: it is the entry, not
   a candidate for one.
   Measured against a real Apple Music media folder, every file came back
   "Unknown Artist / Unknown Album" -- which is precisely where matching a
   streaming library to disk falls apart, and precisely where this path does
   not care. */
let MODE = 'files';          // 'files' = the folder is the library; 'list' = match a track list

/* Path words that carry no identity. They have to be dropped rather than kept
   as context, because the score charges for every token the query does not
   have: a real Apple media folder is
   "Music/Unknown Artist/Unknown Album/Margarita.mp3", and feeding that whole
   path in scored the correct file 0.417 -- four junk tokens were enough to put
   an exact title match below the bar. Filtering them, the same pair scores
   above it. The artist is usually two levels up (Artist/Album/Track), so the
   answer is to filter the path, not to shorten it. */
const JUNK=/^(unknown|various|artists?|albums?|music|media|medialocalized|itunes|library|downloads?|compilations?|singles?|tracks?|songs?|mp3s?|audio|my|the)$/i;
function nameFrom(f){
  const parts=(f.webkitRelativePath||f.name).split('/');
  const base=parts[parts.length-1].replace(/\.[^.]+$/,'');
  const dirs=parts.slice(0,-1)
    .flatMap(d=>d.split(/[\s_\-.]+/))
    .filter(w=>w && !JUNK.test(w) && !/^\d+$/.test(w));
  return { title: base.replace(/^\d+[\s._-]+/,'').trim() || base,
           folder: parts.length>1 ? parts[parts.length-2] : '',
           dirs: dirs.join(' ') };
}
async function indexFiles(list){
  FILES=[];
  const n=list.length;
  for(let i=0;i<n;i++){
    const f=list[i];
    let t={artist:'',title:''};
    try{ t=await tags(f); }catch(e){}
    const nm=nameFrom(f);
    const title  = t.title  || nm.title;
    /* "Unknown Artist" and "Unknown Album" are what Music.app writes when it
       has nothing, and they are what a real media folder is FULL of -- the one
       measured here was every file. Falling back to the parent folder then puts
       the same meaningless word on every row, which is worse than an empty one
       because it looks like data. Any "Unknown ..." is treated as absent. */
    let artist = t.artist || nm.folder || '';
    if(/^unknown\b/i.test(artist) || /^(music|media|downloads?|itunes)$/i.test(artist)) artist='';
    FILES.push({ file:f, artist, title,
                 label:(artist+' '+title).trim(),
                 key:keyOf(artist+' '+title+' '+nm.dirs),
                 secs:null });
    if((i&31)===0) note('Reading tags… '+i+'/'+n);
  }
}
async function pickFolder(ev){
  const list = Array.from(ev.target.files||[]).filter(f=>AUDIO.test(f.name));
  ev.target.value='';
  if(!list.length){ note('No audio files in that folder.', true); return; }
  await useFiles(list);
}
async function useFiles(list){
  note('Reading tags… 0/'+list.length);
  await indexFiles(list);
  if(TRACKS.length){ MODE='list'; note(FILES.length+' files indexed.'); rematch(); }
  else{
    MODE='files';
    FILES.sort((a,b)=>(a.artist||'~').localeCompare(b.artist||'~')||a.title.localeCompare(b.title));
    note(FILES.length+' tracks. Click one to render it.');
    lengths();
  }
  render();
}

/* Durations here are shown, not enforced -- in this mode the file IS the
   track, so there is nothing to disagree with. Capped and cancellable so a
   large folder cannot leave a probe running over thousands of files. */
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

/* ID3v2 (mp3) and the MP4 ilst atoms (m4a), which between them cover a normal
   library. Only the first 256KB is read -- tags live at the front of both
   formats, and reading whole files here would mean reading the entire library
   into memory to build a list. */
async function tags(f){
  const buf = new Uint8Array(await f.slice(0, 262144).arrayBuffer());
  const ascii=(o,n)=>String.fromCharCode.apply(null, buf.subarray(o,o+n));
  let title='', artist='';
  if(ascii(0,3)==='ID3'){
    const v=buf[3];
    const sz=(buf[6]<<21)|(buf[7]<<14)|(buf[8]<<7)|buf[9];   // syncsafe
    let o=10;
    while(o+10 <= Math.min(10+sz, buf.length)){
      const id=ascii(o,4); if(!/^[A-Z0-9]{4}$/.test(id)) break;
      let fs;
      if(v>=4) fs=(buf[o+4]<<21)|(buf[o+5]<<14)|(buf[o+6]<<7)|buf[o+7];   // syncsafe in v2.4
      else     fs=(buf[o+4]<<24)|(buf[o+5]<<16)|(buf[o+6]<<8)|buf[o+7];
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
    // properly means handling meta's 4 version bytes and several optional
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

/* ---------- matching ---------- */
/* Everything that is not the song gets stripped, because it is exactly what
   differs between a streaming catalogue and a local file: "(Remastered 2011)",
   "[Official Video]", "feat. X", a leading track number, an extension. What is
   left is compared as a set of words rather than a string, so word order and a
   missing "the" do not matter. */
const STOP = /\b(feat|ft|featuring|with|official|video|audio|lyrics?|remaster(ed)?|remastered|version|edit|mono|stereo|explicit|bonus|track|hd|hq)\b/g;
function keyOf(s){
  return (s||'').toLowerCase()
    .replace(/\([^)]*\)/g,' ').replace(/\[[^\]]*\]/g,' ')
    .replace(/\.(mp3|m4a|aac|wav|flac|ogg|opus|aif|aiff|wma)\b/g,' ')
    .replace(/[‘’“”]/g,"'")
    .replace(STOP,' ')
    .replace(/[^a-z0-9\s]/g,' ')
    .replace(/\s+/g,' ').trim();
}
const toks = s => new Set(s.split(' ').filter(w=>w.length>1));
/* ASYMMETRIC ON PURPOSE. Jaccard was the obvious choice and it is the wrong
   one, because it punishes the two sides equally for having tokens the other
   lacks -- and the file side is SUPPOSED to have more. A library on disk is
   normally Artist/Album/NN Title, so "Radiohead Karma Police" is being matched
   against "radiohead ok computer 03 karma police": every token of the query is
   present, and Jaccard still scored it 0.500, below any threshold that also
   rejects a wrong track. Measured over a set of 17 real-shaped pairs, that
   layout -- the commonest one there is -- failed on Jaccard and passes here.
   So: reward covering the query, charge only a fraction for the file's extra
   context. alpha 0.35 was picked by sweeping it against those pairs; it puts
   the worst true match at 0.741 and the best false one at 0.597. */
const ALPHA = 0.35;
function score(a,b){
  if(!a||!b) return 0;
  const A=toks(a), B=toks(b);
  if(!A.size||!B.size) return 0;
  let inter=0, extra=0;
  for(const w of A) if(B.has(w)) inter++;
  for(const w of B) if(!A.has(w)) extra++;
  return inter / (A.size + ALPHA*extra);
}
/* 0.65 sits in the middle of the 0.597..0.741 gap that sweep found. */
const BAR = 0.65;
function rematch(){
  MATCH = new Map();
  if(!TRACKS.length || !FILES.length) return;
  TRACKS.forEach((t,i)=>{
    let best=null, bs=0;
    for(const f of FILES){ const s=score(t.key,f.key); if(s>bs){ bs=s; best=f; } }
    if(best && bs>=BAR) MATCH.set(i,{file:best.file,score:bs,secs:null,ok:null});
  });
  verify();
}

/* ---------- duration, which is what actually decides it ----------
   TEXT CANNOT SETTLE THIS AND IT IS WORTH SAYING WHY. A query that is a strict
   SUBSET of a wrong filename scores at the top whatever the metric: "The
   Beatles Yesterday" against "The Beatles - Yesterday and Today" covers every
   query token, and over the 17-pair set it scored 0.811 -- above the worst
   correct match. No threshold separates those two facts, because on the text
   there is nothing to separate.
   Length does separate them, completely. Spotify hands back duration_ms with
   every saved track, so the right answer is to shortlist on text and DECIDE on
   duration. Two recordings of the same track agree to well inside a second;
   two different tracks essentially never do.
   METADATA, NOT decodeAudioData: the duration is in the container header, and
   a full decode of every matched file would read the whole library into memory
   to populate a list. An <audio> element with preload=metadata reads the front
   of the file and stops.
   TOLERANCE 2.5s, which is wide enough for the edit differences that are still
   the same recording -- a fade trimmed, a gapless boundary, a tag padding a
   frame -- and far narrower than the gap between two different songs. */
const TOL = 2.5;
function probe(file){
  return new Promise(res=>{
    const a=document.createElement('audio'), u=URL.createObjectURL(file);
    let done=false;
    const fin=v=>{ if(done) return; done=true; URL.revokeObjectURL(u); a.removeAttribute('src'); res(v); };
    a.preload='metadata';
    a.onloadedmetadata=()=>fin(isFinite(a.duration)?a.duration:null);
    a.onerror=()=>fin(null);
    setTimeout(()=>fin(null), 5000);   // a format the browser will not open
    a.src=u;
  });
}
let verifyRun = 0;
async function verify(){
  const run = ++verifyRun;                 // a newer folder pick cancels this one
  const jobs=[...MATCH.entries()].filter(([,m])=>m.ok===null);
  if(!jobs.length){ render(); return; }
  let n=0, done=0;
  const worker=async()=>{
    while(n<jobs.length && run===verifyRun){
      const [i,m]=jobs[n++];
      const d=await probe(m.file);
      if(run!==verifyRun) return;
      m.secs=d;
      const want=(TRACKS[i]||{}).secs||0;
      // No duration on either side is not a failure, it is an unknown: the file
      // stays usable and simply is not confirmed.
      m.ok = (d==null||!want) ? null : (Math.abs(d-want)<=TOL);
      done++;
      if((done&7)===0){ note('Checking lengths… '+done+'/'+jobs.length); render(); }
    }
  };
  await Promise.all([worker(),worker(),worker(),worker()]);
  if(run!==verifyRun) return;
  const bad=[...MATCH.values()].filter(m=>m.ok===false).length;
  for(const [i,m] of [...MATCH.entries()]) if(m.ok===false) MATCH.delete(i);
  note(bad ? (bad+' rejected: the file is a different length to the track')
           : (MATCH.size?'Lengths confirmed.':''));
  render();
}

/* ---------- ui ---------- */
function note(msg, bad){
  const el=$('libNote'); if(!el) return;
  el.textContent=msg; el.className='libNote'+(bad?' bad':'');
}
const mmss=x=>(x==null||!isFinite(x))?'':(Math.floor(x/60)+':'+String(Math.round(x%60)).padStart(2,'0'));
let FILTER='';
function matchesFilter(a,b){
  if(!FILTER) return true;
  return ((a||'')+' '+(b||'')).toLowerCase().includes(FILTER);
}
function row(title, sub, right, dim){
  const r=document.createElement('div');
  r.className='libRow'+(dim?' un':'');
  r.innerHTML='<span class="libT"></span><span class="libA"></span><span class="libS"></span>';
  r.children[0].textContent=title;
  r.children[1].textContent=sub||'';
  r.children[2].textContent=right||'';
  return r;
}
function render(){
  const list=$('libList'); if(!list) return;
  const frag=document.createDocumentFragment();
  let shown=0;

  if(MODE==='files'){
    if(!FILES.length){
      list.innerHTML='<div class="libEmpty">Choose your music folder — every track in it becomes a row here.</div>';
      $('libCount').textContent='—'; return;
    }
    for(const f of FILES){
      if(!matchesFilter(f.artist,f.title)) continue;
      const r=row(f.title, f.artist, mmss(f.secs), false);
      r.title='Render '+f.title+'\n'+(f.file.webkitRelativePath||f.file.name);
      r.onclick=()=>{
        if(typeof loadFile!=='function'){ note('viewer not ready', true); return; }
        note('Loading '+f.title+'…'); loadFile(f.file);
      };
      frag.appendChild(r); shown++;
    }
    $('libCount').textContent = FILTER ? (shown+' of '+FILES.length) : String(FILES.length);
  } else {
    if(!TRACKS.length){ list.innerHTML='<div class="libEmpty">Connect Spotify, or paste a list.</div>'; return; }
    $('libCount').textContent = MATCH.size+' of '+TRACKS.length+' matched';
    TRACKS.forEach((t,i)=>{
      if(!matchesFilter(t.artist,t.title)) return;
      const m=MATCH.get(i);
      const right = m ? (m.ok===true?mmss(m.secs):(m.secs===null?'…':mmss(t.secs))) : 'no file';
      const r=row(t.title, t.artist, right, !m);
      if(m){
        r.title='Render '+t.title+'\n'+m.file.name+
          (m.ok===true?('\nlength confirmed '+mmss(m.secs)):'\nlength not confirmed');
        r.onclick=async()=>{
          if(typeof loadFile!=='function'){ note('viewer not ready', true); return; }
          /* CONFIRMED AT THE LAST MOMENT, not only in the background sweep. The
             sweep can still be running, and this is the click that spends a
             credit -- so the one file about to be analysed is checked again
             here before anything is handed to the engine. */
          if(m.ok!==true && t.secs){
            note('Checking '+t.title+'…');
            const d = m.secs!=null ? m.secs : await probe(m.file);
            if(d!=null && Math.abs(d-t.secs)>TOL){
              MATCH.delete(i); render();
              note('That file is '+mmss(d)+' but the track is '+mmss(t.secs)+' — wrong recording, not loaded.', true);
              return;
            }
          }
          note('Loading '+t.title+'…'); loadFile(m.file);
        };
      } else {
        r.title='No local file matched. Add the folder that has it, or use Load.';
      }
      frag.appendChild(r); shown++;
    });
  }
  list.innerHTML=''; list.appendChild(frag);
  if(!shown && FILTER) list.innerHTML='<div class="libEmpty">Nothing matches “'+FILTER+'”.</div>';
}

/* A DROPPED FOLDER, because it is the shortest path there is: no dialog, no
   button. dataTransfer.files flattens a directory to nothing, so the entries
   have to be walked -- webkitGetAsEntry is the only way to see a folder at
   all, and readEntries returns at most 100 per call, which is the bug that
   silently truncates every naive implementation of this. */
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
  if(!roots.some(e=>e.isDirectory)) return false;   // a single file: the viewer already handles it
  ev.preventDefault(); ev.stopPropagation();
  const g=$('gLibrary'); if(g) g.open=true;
  note('Reading folder…');
  const out=[];
  for(const r of roots) await walk(r, out, 0);
  if(!out.length){ note('No audio files in there.', true); return true; }
  await useFiles(out);
  return true;
}

function panel(){
  const host=document.getElementById('gCredits');
  if(!host||document.getElementById('gLibrary')) return;
  const css=document.createElement('style');
  css.textContent=`
  #gLibrary .libNote{opacity:.72;font-size:11px;margin:4px 0 2px}
  #gLibrary .libNote.bad{color:#ff8080;opacity:1}
  #gLibrary .libList{max-height:240px;overflow-y:auto;overflow-x:hidden;margin-top:6px;
    border-top:1px solid rgba(255,255,255,.08)}
  #gLibrary .libRow{display:grid;grid-template-columns:1fr auto;gap:0 8px;padding:4px 2px;
    border-bottom:1px solid rgba(255,255,255,.05);cursor:pointer;font-size:11px;line-height:1.25}
  #gLibrary .libRow:hover{background:rgba(255,255,255,.06)}
  #gLibrary .libRow.un{cursor:default;opacity:.42}
  #gLibrary .libRow.un:hover{background:none}
  #gLibrary .libT{grid-column:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #gLibrary .libA{grid-column:1;opacity:.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #gLibrary .libS{grid-column:2;grid-row:1/3;align-self:center;opacity:.55;font-variant-numeric:tabular-nums}
  #gLibrary .libEmpty{opacity:.5;font-size:11px;padding:8px 2px;line-height:1.45}
  #gLibrary input[type=file]{display:none}
  /* The shared .row puts label and .val side by side, which is right for a
     slider and wrong for controls whose labels are two words each -- they
     overlapped at the panel's width. These lay out on their own line. */
  #gLibrary .libBtns{display:flex;flex-wrap:wrap;gap:6px;width:100%}
  #gLibrary .libBtns .btn{flex:0 0 auto;white-space:nowrap}
  #gLibrary .libWide{display:block}
  #gLibrary .libWide>label{display:block;margin-bottom:3px}
  #gLibrary .libWide>.val{display:block;width:100%;text-align:left}
  #gLibrary textarea,#gLibrary #libFilter{width:100%;font:inherit;font-size:11px;line-height:1.35;
    background:rgba(0,0,0,.25);color:inherit;border:1px solid rgba(255,255,255,.12);
    border-radius:4px;padding:4px 6px}
  #gLibrary textarea{resize:vertical}
  #gLibrary .libMore{margin-top:8px;border-top:1px solid rgba(255,255,255,.08);padding-top:6px}
  #gLibrary .libMore>summary{opacity:.62;font-size:11px;cursor:pointer;list-style:none}
  #gLibrary .libMore>summary::-webkit-details-marker{display:none}
  #gLibrary .libMore>summary:before{content:"+ ";opacity:.7}
  #gLibrary .libMore[open]>summary:before{content:"- "}`;
  document.head.appendChild(css);
  const d=document.createElement('details');
  d.id='gLibrary';
  /* THE FOLDER IS FIRST AND ALONE AT THE TOP. Everything that needs a
     developer account is behind the fold, because none of it is required to
     use this and putting it first implied that it was. */
  d.innerHTML =
    '<summary>Library</summary>'+
    '<div class="row libWide"><label></label><span class="val"><span class="libBtns">'+
      '<label class="btn" for="libFolder" title="Your music folder. It is read in the browser — nothing is uploaded until you pick a track.">Choose music folder</label>'+
      '</span><input id="libFolder" type="file" webkitdirectory directory multiple></span></div>'+
    '<div class="row libWide"><label></label><span class="val">'+
      '<input type="text" id="libFilter" placeholder="Filter…" autocomplete="off" spellcheck="false"></span></div>'+
    '<div class="row"><label>Tracks</label><span class="val" id="libCount">&mdash;</span></div>'+
    '<div class="libNote" id="libNote"></div>'+
    '<div class="libList" id="libList"></div>'+
    '<details class="libMore"><summary>Match a streaming library</summary>'+
      '<div class="libNote">Lists what you saved on a service and finds each one in your folder. '+
      'The audio still comes from your file — no service will hand a web page decoded audio.</div>'+
      '<div class="row libWide"><label>Paste a list</label>'+
        '<textarea id="libPaste" rows="3" placeholder="Artist - Title, one per line&#10;or an exported playlist (Music.app: File &gt; Library &gt; Export Playlist)" '+
        'title="Needs no developer account, and an exported playlist brings its durations with it so the length check still runs."></textarea></div>'+
      '<div class="row libWide"><label></label><span class="val"><span class="libBtns">'+
        '<button type="button" id="libUse" class="btn">Use list</button>'+
        '<button type="button" id="libClear" class="btn">Back to folder</button></span></span></div>'+
      '<div class="row libWide"><label>Spotify client id</label>'+
        '<input type="text" id="libCid" placeholder="from developer.spotify.com" autocomplete="off" spellcheck="false" '+
        'title="Register '+REDIRECT+' as the redirect URI — a loopback IP is the only http form Spotify still accepts. A new app is also capped at 25 users until Spotify grants a quota extension. Stored in this browser only."></div>'+
      '<div class="row libWide"><label></label><span class="val"><span class="libBtns">'+
        '<button type="button" id="libConn" class="btn">Connect Spotify</button></span></span></div>'+
    '</details>';
  host.parentNode.insertBefore(d, host);
  const cid=$('libCid');
  cid.value=clientId();
  cid.addEventListener('change',()=>localStorage.setItem(LS.cid,cid.value.trim()));
  $('libConn').addEventListener('click',connect);
  $('libFolder').addEventListener('change',pickFolder);
  $('libUse').addEventListener('click',useList);
  $('libClear').addEventListener('click',()=>{
    TRACKS=[]; MATCH=new Map(); MODE='files';
    note(FILES.length?(FILES.length+' tracks. Click one to render it.'):''); render();
  });
  const flt=$('libFilter');
  flt.addEventListener('input',()=>{ FILTER=flt.value.trim().toLowerCase(); render(); });
  // Capture phase: the viewer has its own window-level drop handler for a
  // single audio file, and a dropped FOLDER has to be claimed before it.
  addEventListener('dragover',e=>e.preventDefault());
  addEventListener('drop',e=>{ onDrop(e); }, true);
  render();
}

async function boot(){
  panel();
  const came = await handleCallback();
  if(!came && getJSON(LS.tok)) { try { await loadLibrary(); } catch(e){ note('Connect Spotify to list your saved tracks.'); } }
}
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot);
else boot();

// for measurement and for the console
window.__LIB = { get tracks(){return TRACKS;}, get files(){return FILES;}, get match(){return MATCH;},
                 keyOf, score, rematch, render, probe, parseList, useList, useFiles,
                 get mode(){return MODE;}, BAR, ALPHA, TOL };
})();
