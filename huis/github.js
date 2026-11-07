// De importpagina op vangenderingen.nl: in plaats van server.py praat hij met GitHub.
// - Licht werk hier in de browser: de status (uit de kluis), categorie raden, draaien/kantelen/eenheid/schalen
//   (normaliseer, net als importeer.py), downloaden.
// - Zwaar werk als GitHub Action (.github/workflows/huis-importer.yml + huis-tools/runner.py, de echte importer):
//   pagina lezen, model ophalen, toevoegen, hitboxes/anker/categorie opslaan, kleiner maken, verven, verwijderen.
// sync.py zet deze module (en slot.js) vóór het script van importeer.html; die gebruikt window.huisBackend.
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import * as THREE from 'three';

const S = window.HuisSlot;
const API = S.apiBasis();
const R = `${API}/repos/${S.REPO}`;
const WORKFLOW = 'huis-importer.yml';
const tekst = new TextEncoder(), lees = new TextDecoder();

/* ---------- sleutels en token ---------- */

let sleutels = null, token = null, tokenKlaar = null;
async function klaar() {
  sleutels = sleutels || await S.sleutels();
  if (!sleutels) { location.replace('/huis/?naar=' + encodeURIComponent(location.pathname)); throw new Error('Vergrendeld'); }
  token = token || await S.token();
  if (!token) token = await (tokenKlaar = tokenKlaar || vraagToken());
  return token;
}

async function gh(pad, opties = {}) {
  await klaar();
  const r = await fetch(pad.startsWith('http') ? pad : R + pad, {
    cache: 'no-store', ...opties,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', ...opties.headers },
  });
  if (r.status === 401) {                     // token verlopen of ingetrokken
    token = null; tokenKlaar = null; await S.bewaarToken(null);
    throw new Error('GitHub accepteert het token niet meer. Herlaad de pagina en plak een nieuw token.');
  }
  return r;
}
async function ghJson(pad, opties) {
  const r = await gh(pad, opties);
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    const e = new Error(`GitHub gaf ${r.status}${d.message ? ': ' + d.message : ''}`); e.status = r.status; throw e;
  }
  return r.status === 204 ? null : r.json();
}
async function ruw(pad, ref) {
  const r = await gh(`/contents/${pad}?ref=${encodeURIComponent(ref)}`, { headers: { Accept: 'application/vnd.github.raw' } });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`GitHub gaf ${r.status} bij het lezen`);
  return r.arrayBuffer();
}

/* ---------- de kluis (main) ---------- */

const kluisCache = new Map();               // `${sha}:${pad}` -> ArrayBuffer (per commit onveranderlijk)
async function hoofd() { return (await ghJson(`/git/ref/heads/${S.TAK}`)).object.sha; }
async function kluis(pad, sha) {
  const sleutel = `${sha}:${pad}`;
  if (!kluisCache.has(sleutel)) {
    kluisCache.set(sleutel, (async () => {
      const blob = await ruw(`huis/kluis/${await S.naam(sleutels, pad)}`, sha);
      return blob ? S.ontsleutel(sleutels, blob, pad) : null;
    })());
  }
  try { return await kluisCache.get(sleutel); } catch (e) { kluisCache.delete(sleutel); throw e; }
}
async function kluisJson(pad, sha) { const b = await kluis(pad, sha); return b ? JSON.parse(lees.decode(b)) : null; }

let importerInfo = { categorieen: [], raden: [], max_driehoeken: 150000, meshy: false };
async function status() {
  await klaar();
  const sha = await hoofd();
  const [idx, cat, web, verven, info] = await Promise.all([
    kluisJson('_index', sha), kluisJson('project/Meubels/catalogus.json', sha), kluisJson('web/meubels/catalogus.json', sha),
    kluisJson('project/Meubels/verven.json', sha), kluisJson('importer.json', sha)]);
  if (!idx) throw new Error('De kluis is nog leeg. Draai op de Mac eerst huis-tools/sync.sh.');
  importerInfo = { ...importerInfo, ...info };
  const b = idx.bestanden || {}, inWeb = new Set((web?.meubels || []).map((m) => m.id));
  // zoals _catalogus() in server.py
  const catalogus = (cat?.meubels || []).map((r) => {
    const orig = b['project/Meubels/' + r.bestand.replace(/\.glb$/, '.origineel.glb')];
    const bytes = b['project/Meubels/' + r.bestand]?.grootte ?? null;
    return { ...r, in_huismodel: inWeb.has(r.id), bytes, heeft_origineel: !!orig, bytes_origineel: orig ? orig.grootte : bytes };
  });
  return { meshy: !!importerInfo.meshy, env: 'GitHub → Settings → Secrets → Actions: MESHY_API_KEY', catalogus,
           categorieen: importerInfo.categorieen, verven: verven?.verven || [] };
}

/* ---------- de tak huis-werk: concepten, uploads en antwoorden ---------- */

const vandaag = () => new Date().toISOString().slice(0, 10);
const willekeurig = (n) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (x) => x.toString(16).padStart(2, '0')).join('');

async function werkLees(ref) {
  const blob = await ruw(ref, S.WERKTAK);
  return blob ? S.ontsleutel(sleutels, blob, ref) : null;
}

// Bestanden in één commit op huis-werk zetten (de Action ruimt oude dagen op). Geeft de verwijzingen.
async function werkSchrijf(bestanden) {
  const datum = vandaag(), boom = [], refs = [];
  for (const data of bestanden) {
    const ref = `w/${datum}/${willekeurig(16)}`;
    const blob = await S.versleutel(sleutels, new Uint8Array(data), ref);
    const { sha } = await ghJson('/git/blobs', { method: 'POST', body: JSON.stringify({ content: S.naarB64(blob), encoding: 'base64' }) });
    boom.push({ path: ref, mode: '100644', type: 'blob', sha }); refs.push(ref);
  }
  for (let poging = 0; poging < 5; poging++) {
    let ouder = null;
    try { ouder = (await ghJson(`/git/ref/heads/${S.WERKTAK}`)).object.sha; } catch (e) { if (e.status !== 404) throw e; }
    const basis = ouder ? (await ghJson(`/git/commits/${ouder}`)).tree.sha : undefined;
    const t = await ghJson('/git/trees', { method: 'POST', body: JSON.stringify({ base_tree: basis, tree: boom }) });
    const c = await ghJson('/git/commits', { method: 'POST', body: JSON.stringify({ message: 'werk', tree: t.sha, parents: ouder ? [ouder] : [] }) });
    try {
      if (ouder) await ghJson(`/git/refs/heads/${S.WERKTAK}`, { method: 'PATCH', body: JSON.stringify({ sha: c.sha, force: false }) });
      else await ghJson('/git/refs', { method: 'POST', body: JSON.stringify({ ref: `refs/heads/${S.WERKTAK}`, sha: c.sha }) });
      return refs;
    } catch (e) { if (e.status !== 422 && e.status !== 409) throw e; }   // tussendoor veranderd: opnieuw
  }
  throw new Error('Kon de bestanden niet naar GitHub sturen.');
}

/* ---------- een Action starten en op het antwoord wachten ---------- */

const NAMEN = {
  lees: 'Pagina lezen', maak: 'Model ophalen', eigen: 'Model maken', bewaar: 'Toevoegen aan het project',
  hitboxes_auto: 'Hitboxes voorstellen', hitboxes: 'Hitboxes opslaan', anker: 'Ankerpunt opslaan', categorie: 'Categorie opslaan',
  verf_lees: 'Verfpagina lezen', verf_kleur: 'Kleur uit foto', verf_bewaar: 'Verf toevoegen', verf_verwijder: 'Verf verwijderen',
  optimaliseer: 'Kleinere versie maken', optimaliseer_bewaar: 'Kleinere versie opslaan', huismodel: 'Huismodel bijwerken',
  verwijder: 'Verwijderen',
};
const lopend = new Map();                   // id -> {naam, tekst, url}

async function actie(naam, body, voortgang = () => {}) {
  await klaar();
  const id = willekeurig(8), datum = vandaag();
  const zet = (t, url) => { lopend.set(id, { naam: NAMEN[naam] || naam, tekst: t, url }); toonLopend(); voortgang(t); };
  zet('Naar GitHub sturen…');
  try {
    let verzoek = S.naarB64(await S.versleutel(sleutels, tekst.encode(JSON.stringify({ id, datum, actie: naam, body })), 'verzoek'));
    if (verzoek.length > 60000) [verzoek] = await werkSchrijf([tekst.encode(JSON.stringify({ id, datum, actie: naam, body }))]);
    await ghJson(`/actions/workflows/${WORKFLOW}/dispatches`, { method: 'POST', body: JSON.stringify({ ref: S.TAK, inputs: { id, verzoek } }) });
    const begin = Date.now(), ref = `w/${datum}/${id}`;
    let run = null, klaarGezien = 0;
    for (let i = 0; Date.now() - begin < 40 * 60e3; i++) {
      await new Promise((ok) => setTimeout(ok, i < 3 ? 2500 : 3500));
      const blob = await werkLees(ref).catch(() => null);
      if (blob) {
        const a = JSON.parse(lees.decode(blob));
        if (a._uitvoer) console.debug(`[huis ${naam}]`, a._uitvoer);
        if (a.fout && !a.taak) { const e = new Error(a.fout); e.antwoord = a; throw e; }
        return a;
      }
      if (i % 2 === 0) {
        const runs = await ghJson(`/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=15`).catch(() => null);
        run = runs?.workflow_runs?.find((x) => x.display_title === 'huis ' + id) || run;
      }
      const sec = Math.round((Date.now() - begin) / 1000), tijd = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
      if (!run) zet(`Wachten op GitHub… ${tijd}`);
      else if (run.status === 'completed') {
        if (++klaarGezien > 3) throw new Error(`De GitHub-Action ${run.conclusion === 'success' ? 'gaf geen antwoord' : 'mislukte'}. Bekijk ${run.html_url}`);
      } else zet(`${run.status === 'queued' ? 'In de wachtrij' : 'GitHub is bezig'}… ${tijd}`, run.html_url);
    }
    throw new Error('GitHub deed er te lang over.');
  } finally { lopend.delete(id); toonLopend(); }
}

/* ---------- normaliseer, zoals importeer.py ---------- */

function leesGlb(buf) {
  const v = new DataView(buf);
  if (v.getUint32(0, true) !== 0x46546C67 || v.getUint32(4, true) !== 2) throw new Error('geen glTF 2.0 binary');
  let off = 12, gltf = null, bin = new Uint8Array(0);
  while (off < buf.byteLength) {
    const n = v.getUint32(off, true), soort = v.getUint32(off + 4, true), stuk = new Uint8Array(buf, off + 8, n);
    if (soort === 0x4E4F534A) gltf = JSON.parse(lees.decode(stuk));
    else if (soort === 0x004E4942) bin = stuk;
    off += 8 + n;
  }
  return { gltf, bin };
}
function schrijfGlb(gltf, bin) {
  let js = tekst.encode(JSON.stringify(gltf));
  const jsPad = (4 - (js.length % 4)) % 4, binPad = (4 - (bin.length % 4)) % 4;
  const totaal = 12 + 8 + js.length + jsPad + (bin.length ? 8 + bin.length + binPad : 0);
  const uit = new Uint8Array(totaal), v = new DataView(uit.buffer);
  v.setUint32(0, 0x46546C67, true); v.setUint32(4, 2, true); v.setUint32(8, totaal, true);
  v.setUint32(12, js.length + jsPad, true); v.setUint32(16, 0x4E4F534A, true);
  uit.set(js, 20); uit.fill(0x20, 20 + js.length, 20 + js.length + jsPad);
  if (bin.length) {
    const o = 20 + js.length + jsPad;
    v.setUint32(o, bin.length + binPad, true); v.setUint32(o + 4, 0x004E4942, true); uit.set(bin, o + 8);
  }
  return uit;
}

const loader = new GLTFLoader().setDRACOLoader(new DRACOLoader().setDecoderPath('https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/libs/draco/gltf/'));
const EENHEDEN = { m: 1.0, cm: 0.01, mm: 0.001 };
const r1 = (x) => Math.round(x * 10) / 10;

async function normaliseer(raw, doel, schaalNaarDoel, draai, kantel, eenheid) {
  draai = ((Math.trunc(draai) % 360) + 360) % 360;
  const scene = (await loader.parseAsync(raw.slice(0), '')).scene;
  const geos = new Set(); let tris = 0;
  scene.traverse((o) => { if (o.isMesh && !geos.has(o.geometry)) { geos.add(o.geometry); tris += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3; } });
  const m = new THREE.Matrix4();
  if (draai) m.multiply(new THREE.Matrix4().makeRotationY(THREE.MathUtils.degToRad(draai)));
  if (kantel) m.multiply(new THREE.Matrix4().makeRotationX(-Math.PI / 2));
  const g = new THREE.Group(); g.add(scene); g.matrixAutoUpdate = false; g.matrix.copy(m); g.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(g, true), lo = box.min.toArray(), hi = box.max.toArray();
  const ext = hi.map((h, i) => h - lo[i]);
  let s = EENHEDEN[eenheid];
  if (s === undefined) { const mx = Math.max(...ext); s = mx <= 20 ? 1 : mx < 2000 ? 0.01 : 0.001; }
  let sv = [s, s, s];
  if (schaalNaarDoel && doel && 'b' in doel && 'd' in doel && 'h' in doel) {
    let d = [doel.b / 100, doel.h / 100, doel.d / 100];
    if ((d[0] > d[2]) !== (ext[0] > ext[2])) d = [d[2], d[1], d[0]];   // AI-model ligt dwars
    sv = d.map((x, i) => x / Math.max(ext[i], 1e-6));
  }
  const centrum = [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2];
  const { gltf, bin } = leesGlb(raw);
  const sc = gltf.scene ?? 0;
  let kinderen = gltf.scenes[sc].nodes || [];
  const nodes = gltf.nodes = gltf.nodes || [];
  if (kantel) { const h = -Math.PI / 4; nodes.push({ name: 'meubel_kantel', children: kinderen, rotation: [Math.sin(h), 0, 0, Math.cos(h)] }); kinderen = [nodes.length - 1]; }
  if (draai) { const h = THREE.MathUtils.degToRad(draai) / 2; nodes.push({ name: 'meubel_draai', children: kinderen, rotation: [0, Math.sin(h), 0, Math.cos(h)] }); kinderen = [nodes.length - 1]; }
  nodes.push({ name: 'meubel_wortel', children: kinderen, scale: sv, translation: centrum.map((c, i) => -c * sv[i]) });
  gltf.scenes[sc].nodes = [nodes.length - 1];
  return { glb: schrijfGlb(gltf, bin), maten: { b: r1(ext[0] * sv[0] * 100), h: r1(ext[1] * sv[1] * 100), d: r1(ext[2] * sv[2] * 100) }, tris: Math.round(tris) };
}

function controleer(maten, web, tris) {
  const uit = [];
  if (web && 'b' in web && 'd' in web && 'h' in web) {
    const a = [maten.b, maten.d].sort((x, y) => x - y), w = [web.b, web.d].sort((x, y) => x - y);
    const afw = Math.max(Math.abs(a[0] - w[0]), Math.abs(a[1] - w[1]), Math.abs(maten.h - web.h));
    if (afw > 5) uit.push(`wijkt tot ${Math.round(afw)} cm af van de webshopmaten ${JSON.stringify(web)}`);
  }
  if (tris > importerInfo.max_driehoeken) uit.push(`${tris} driehoeken is zwaar voor de game`);
  return uit;
}

/* ---------- taken (een model in de maak) ---------- */

const taken = new Map();
const urls = new Map();                      // taak -> blob-URL van het getoonde model
function blobUrl(taak, data) {
  if (urls.has(taak)) URL.revokeObjectURL(urls.get(taak));
  const u = URL.createObjectURL(new Blob([data], { type: 'model/gltf-binary' }));
  urls.set(taak, u); return u;
}
function resultaat(t, glb, maten, tris) {
  t.maten = maten; t.tris = tris;
  return { glb: blobUrl(t.id, glb), maten, maten_webshop: t.info.maten_cm, driehoeken: tris, herkomst: t.herkomst,
           draai: t.draai, schaal: t.schaal, kantel: t.kantel, eenheid: t.eenheid, eigen: t.herkomst?.soort === 'eigen-bestand',
           mb: Math.round(glb.byteLength / 1e3) / 1e3, waarschuwingen: controleer(maten, t.info.maten_cm, tris) };
}
// Antwoord van de Action (Acties.concept) -> de taak hier
async function vulTaak(t, a) {
  t.log = a.log || []; t.fout = a.fout || null;
  if (a.status !== 'klaar') { t.status = a.status || 'fout'; return; }
  const r = a.resultaat;
  Object.assign(t, { info: a.info, herkomst: r.herkomst, draai: r.draai, schaal: r.schaal, kantel: r.kantel, eenheid: r.eenheid,
                     bestanden: a.bestanden, meubelId: a.id });
  const [raw, model] = await Promise.all([werkLees(a.bestanden['raw.glb']), werkLees(a.bestanden['model.glb'])]);
  if (!raw || !model) throw new Error('Het concept is niet meer op GitHub te vinden.');
  t.raw = raw;
  t.resultaat = { ...resultaat(t, model, r.maten, r.driehoeken), waarschuwingen: r.waarschuwingen };
  t.status = 'klaar';
}
function nieuweTaak(info, extra = {}) {
  const t = { id: willekeurig(5), status: 'bezig', log: [], fout: null, info: JSON.parse(JSON.stringify(info)),
              draai: 0, schaal: false, kantel: false, eenheid: 'auto', herkomst: null, ...extra };
  taken.set(t.id, t); return t;
}
function opAchtergrond(t, werk) {
  werk().catch((err) => { t.status = 'fout'; t.fout = err.message; t.log = [...t.log, 'Fout: ' + err.message]; });
}
const metLog = (t) => (regel) => { t.log = [regel]; };
function taak(b) {
  const t = taken.get(b?.taak);
  if (!t) throw new Error('Onbekende of verlopen taak. Begin opnieuw met de link.');
  return t;
}

/* ---------- eigen bestanden ---------- */

const bekeken = new Map();                   // url -> antwoord van lees (met het alvast opgehaalde concept)
const verfInfo = new Map();                  // url -> info van verf_lees
function slug(t) {
  const van = 'äöüëïéèáàóòâêôç', naar = 'aoueieeaaooaeoc';
  t = t.toLowerCase().replace(/[äöüëïéèáàóòâêôç]/g, (c) => naar[van.indexOf(c)]).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return t.length <= 40 ? t : t.slice(0, 41).replace(/-[^-]*$/, '');
}
function bestandNaam(naam) {                  // importeer.bestand_info
  let b = String(naam).split('/').pop(); const p = b.lastIndexOf('.');
  if (p > 0) b = b.slice(0, p);
  return b.replace(/[_-]+/g, ' ').trim() || 'meubel';
}

/* ---------- kleiner maken: alleen het laatste voorbeeld telt ---------- */

let optBezig = false, optWacht = null;
function optimaliseer(b) {
  return new Promise((ok, nee) => {
    if (optWacht) optWacht.nee(new Error('vervangen door een nieuwere instelling'));
    optWacht = { b, ok, nee };
    if (!optBezig) volgendeOpt();
  });
}
async function volgendeOpt() {
  const w = optWacht; optWacht = null;
  if (!w) return;
  optBezig = true;
  try {
    const a = await actie('optimaliseer', w.b);
    const glb = await werkLees(a.glb);
    if (urls.has('opt')) URL.revokeObjectURL(urls.get('opt'));
    urls.set('opt', URL.createObjectURL(new Blob([glb], { type: 'model/gltf-binary' })));
    w.ok({ ...a, glb: urls.get('opt') });
  } catch (err) { w.nee(err); }
  finally { optBezig = false; volgendeOpt(); }
}

/* ---------- de api zoals server.py hem aanbiedt ---------- */

async function api(pad, b = {}) {
  if (pad === 'status') return status();
  if (pad.startsWith('taak/')) {
    const t = taken.get(pad.slice(5));
    if (!t) throw new Error('onbekende taak');
    return { status: t.status, log: t.log, fout: t.fout, ...(t.status === 'klaar' ? { resultaat: t.resultaat } : {}) };
  }
  switch (pad) {
    case 'lees': {
      const a = await actie('lees', { url: b.url });
      bekeken.set(b.url, a);
      const { voorraad, ...rest } = a;
      return rest;
    }
    case 'maak': {
      const gelezen = bekeken.get(b.url);
      if (!gelezen) throw new Error('Lees eerst de pagina.');
      const info = { ...gelezen.info, maten_cm: b.maten && Object.keys(b.maten).length ? b.maten : gelezen.info.maten_cm };
      const t = nieuweTaak(info);
      const v = gelezen.voorraad;
      if (!b.ai && v?.status === 'klaar') {              // al opgehaald bij het lezen
        opAchtergrond(t, async () => {
          await vulTaak(t, v);
          t.info.maten_cm = info.maten_cm;
          t.resultaat = { ...t.resultaat, maten_webshop: info.maten_cm, waarschuwingen: controleer(t.maten, info.maten_cm, t.tris) };
        });
      } else if (!b.ai && v && v.status !== 'klaar') {
        Object.assign(t, { status: v.status === 'geen_model' ? 'geen_model' : 'fout', log: v.log || [], fout: v.fout });
      } else {
        t.log = ['Aan GitHub gevraagd…'];
        opAchtergrond(t, async () => vulTaak(t, await actie('maak', { url: b.url, info, maten: b.maten, ai: !!b.ai, foto: b.foto || 0 }, metLog(t))));
      }
      return { taak: t.id };
    }
    case 'eigen': {
      const info = { url: 'bestand:' + bestandNaam(b.naam), winkel: 'eigen bestand', naam: bestandNaam(b.naam), sku: '', merk: null,
                     fotos: [], maten_cm: b.maten || {}, modellen: [] };
      const t = nieuweTaak(info, { status: 'wacht', eigen: true, uploads: [], naam: b.naam, maten: b.maten });
      return { taak: t.id, id: slug('eigen ' + info.naam) };
    }
    case 'eigen_klaar': {
      const t = taak(b);
      t.status = 'bezig'; t.log = ['Bestanden naar GitHub sturen…'];
      opAchtergrond(t, async () => {
        const refs = await werkSchrijf(await Promise.all(t.uploads.map((u) => u.file.arrayBuffer())));
        const bestanden = t.uploads.map((u, i) => ({ pad: u.pad, ref: refs[i] }));
        await vulTaak(t, await actie('eigen', { naam: t.naam, maten: t.maten, bestanden }, metLog(t)));
      });
      return { taak: t.id };
    }
    case 'pas_aan': {
      const t = taak(b);
      if (t.status !== 'klaar') throw new Error('Het model is nog niet klaar.');
      if ('draai' in b) t.draai = ((Number(b.draai) % 360) + 360) % 360;
      if ('schaal' in b) t.schaal = !!b.schaal;
      if ('maten' in b) t.info.maten_cm = Object.fromEntries(Object.entries(b.maten || {}).filter(([, v]) => v).map(([k, v]) => [k, Number(v)]));
      if ('kantel' in b) t.kantel = !!b.kantel;
      if (['auto', 'm', 'cm', 'mm'].includes(b.eenheid)) t.eenheid = b.eenheid;
      const n = await normaliseer(t.raw, t.info.maten_cm, t.schaal, t.draai, t.kantel, t.eenheid);
      return (t.resultaat = resultaat(t, n.glb, n.maten, n.tris));
    }
    case 'bewaar': {
      const t = taak(b);
      const bestanden = Object.fromEntries(Object.entries(t.bestanden).filter(([k]) => k !== 'model.glb'));
      const a = await actie('bewaar', { info: t.info, id: t.meubelId, herkomst: t.herkomst, draai: t.draai, schaal: t.schaal,
                                        kantel: t.kantel, eenheid: t.eenheid, bestanden, naam: b.naam, anker: b.anker, categorie: b.categorie });
      taken.delete(t.id);
      return a;
    }
    case 'weg': taken.delete(b.taak); return { ok: true };
    case 'categorie_raad': {
      const n = String(b.naam || '').toLowerCase();
      return { categorie: (importerInfo.raden.find(([, p]) => new RegExp(p).test(n)) || ['Overig'])[0] };
    }
    case 'verf_lees': {
      const a = await actie('verf_lees', { url: b.url });
      verfInfo.set(b.url, a.info);
      return a;
    }
    case 'verf_kleur': case 'verf_bewaar':
      if (!verfInfo.has(b.url)) throw new Error('Lees eerst de pagina.');
      return actie(pad, { ...b, info: verfInfo.get(b.url) });
    case 'optimaliseer': return optimaliseer(b);
    case 'toon': {
      const sha = await hoofd(), regel = (await kluisJson('project/Meubels/catalogus.json', sha))?.meubels.find((m) => m.id === b.id);
      if (!regel) throw new Error('Niet in de catalogus.');
      const data = await kluis('project/Meubels/' + regel.bestand, sha);
      const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([data], { type: 'model/gltf-binary' })), download: regel.bestand.split('/').pop() });
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 60e3);
      return { ok: true };
    }
    case 'hitboxes_auto': case 'hitboxes': case 'anker': case 'categorie': case 'verf_verwijder':
    case 'optimaliseer_bewaar': case 'huismodel': case 'verwijder':
      return actie(pad, b);
    default: throw new Error('onbekende actie: ' + pad);
  }
}

function upload(taakId, pad, file) {
  const t = taken.get(taakId);
  if (!t || t.status !== 'wacht') throw new Error('Onbekende taak.');
  t.uploads.push({ pad, file });
}

/* ---------- token vragen ---------- */

function vraagToken() {
  return new Promise((ok) => {
    const d = document.createElement('dialog'); d.className = 'huis-token';
    d.innerHTML = `
      <form method="dialog">
        <h2>GitHub koppelen</h2>
        <p>De importer laat GitHub het zware werk doen. Daarvoor heeft deze browser eenmalig een token nodig.</p>
        <ol>
          <li>Open <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">github.com → nieuw fine-grained token ↗</a>.</li>
          <li>Naam bijvoorbeeld <i>Huis importer</i>, verloopt na een jaar.</li>
          <li><b>Repository access</b>: <i>Only select repositories</i> → <code>${S.REPO.split('/')[1]}</code>.</li>
          <li><b>Permissions → Repository</b>: <i>Actions</i> en <i>Contents</i> op <b>Read and write</b>.</li>
          <li>Klik <b>Generate token</b> en plak hem hieronder.</li>
        </ol>
        <input name="token" type="password" placeholder="github_pat_…" required autocomplete="off" aria-label="GitHub-token">
        <div class="fout" hidden></div>
        <div class="rij"><button class="btn primary grow" value="ok">Koppelen</button></div>
        <p class="small muted">Het token blijft alleen in deze browser. <b>Vergrendelen</b> op de startpagina wist het.</p>
      </form>`;
    document.body.append(d);
    const fout = d.querySelector('.fout'), knop = d.querySelector('button');
    d.addEventListener('cancel', (e) => e.preventDefault());
    d.querySelector('form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const t = d.querySelector('input').value.trim();
      knop.disabled = true; knop.textContent = 'Controleren…'; fout.hidden = true;
      try {
        const h = { Authorization: 'Bearer ' + t, Accept: 'application/vnd.github+json' };
        const a = await fetch(`${R}/git/ref/heads/${S.TAK}`, { headers: h, cache: 'no-store' });
        const b = await fetch(`${R}/actions/workflows/${WORKFLOW}`, { headers: h, cache: 'no-store' });
        if (!a.ok) throw new Error(a.status === 401 ? 'Dit token kent GitHub niet.' : `Geen toegang tot de repo (${a.status}). Staat hij bij Repository access?`);
        if (!b.ok) throw new Error(`Geen toegang tot Actions (${b.status}). Zet Actions op Read and write.`);
        await S.bewaarToken(t);
        d.close(); d.remove(); ok(t);
      } catch (err) { fout.hidden = false; fout.textContent = err.message; }
      finally { knop.disabled = false; knop.textContent = 'Koppelen'; }
    });
    d.showModal(); d.querySelector('input').focus();
  });
}

/* ---------- wat GitHub aan het doen is ---------- */

let balk = null;
function toonLopend() {
  if (!balk) { balk = document.createElement('div'); balk.className = 'huis-lopend'; balk.setAttribute('role', 'status'); document.body.append(balk); }
  balk.hidden = !lopend.size;
  balk.innerHTML = '';
  for (const l of lopend.values()) {
    const r = document.createElement('div');
    r.innerHTML = '<span class="spin"></span><b></b><span class="muted"></span>';
    r.querySelector('b').textContent = l.naam;
    r.lastChild.textContent = l.tekst;
    if (l.url) { const a = Object.assign(document.createElement('a'), { href: l.url, target: '_blank', rel: 'noopener', textContent: 'log ↗' }); r.append(a); }
    balk.append(r);
  }
}

const stijl = document.createElement('style');
stijl.textContent = `
  .huis-lopend { position: fixed; right: 16px; top: 16px; z-index: 50; display: grid; gap: 6px; max-width: min(420px, calc(100vw - 32px)); }
  .huis-lopend[hidden] { display: none; }
  #h-huis a, #k-stappen a { color: var(--accent); }
  .huis-lopend > div { display: flex; gap: 8px; align-items: center; background: var(--paper); border: 1px solid var(--line);
    border-radius: 10px; padding: 8px 12px; box-shadow: 0 4px 16px rgb(0 0 0 / .12); font-size: 13px; }
  .huis-lopend .muted { flex: 1; min-width: 0; }
  .huis-lopend a { color: var(--muted); font-size: 12px; }
  .huis-lopend .spin { flex: none; }
  dialog.huis-token { border: 1px solid var(--line); border-radius: 14px; background: var(--paper); color: var(--ink);
    max-width: min(480px, calc(100vw - 32px)); padding: 22px; }
  dialog.huis-token::backdrop { background: rgb(0 0 0 / .35); }
  dialog.huis-token h2 { margin-bottom: 8px; }
  dialog.huis-token ol { padding-left: 20px; margin: 10px 0 14px; }
  dialog.huis-token li { margin: 4px 0; }
  dialog.huis-token input { width: 100%; padding: 9px 11px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: inherit; font: inherit; }
  dialog.huis-token .rij { display: flex; margin-top: 12px; }
  dialog.huis-token .fout { margin-top: 8px; color: var(--warn); }
  dialog.huis-token a { color: var(--accent); }
  dialog.huis-token code { background: var(--soft); padding: 1px 5px; border-radius: 5px; }`;
document.head.append(stijl);

/* ---------- teksten die op de site anders zijn ---------- */

function zetTekst(sel, html) { const el = document.querySelector(sel); if (el) el.innerHTML = html; }
zetTekst('#h-huis', '<a href="../model/" target="_blank" rel="noopener">Huismodel openen ↗</a> <span class="small muted">nieuwe meubels staan erin zodra GitHub klaar is (paar minuten)</span> · <a href="../" class="small">startpagina</a>');
zetTekst('#k-stappen', '<li>Over een paar minuten staat het in het <a href="../model/" target="_blank" rel="noopener">huismodel</a> onder <b>+ Meubel toevoegen</b> (herlaad het).</li><li>Op de Mac komt het in Huis inspo bij de volgende <code>huis-tools/sync.sh</code>.</li>');
zetTekst('#c-uitleg', 'Alles hier staat ook in het huismodel op de site (na een paar minuten). 📂 downloadt het bestand. Zware stappen doet GitHub: rechtsonder zie je wat er loopt.');
zetTekst('#b-finder', 'Bestand downloaden');

window.huisBackend = {
  api, upload,
  teksten: {
    klaarVoorHuis: 'Staat over een paar minuten in het huismodel op de site.',
    ankerKlaar: 'Staat over een paar minuten in het huismodel op de site.',
    finder: 'Downloaden', finderLang: 'downloaden',
    verfKlaar: 'staat bij de verven en over een paar minuten in het huismodel.',
    inProject: (id) => ` staat in het project (${id}).`,
    meshyUitleg: 'Er is nog geen Meshy-key. Zet MESHY_API_KEY als geheim bij GitHub (Settings → Secrets → Actions) en zet in Huis inspo "meshy" aan.',
  },
};
