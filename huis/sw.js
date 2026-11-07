// Service worker van het huis: ontsleutelt huis/kluis/ voor de pagina's onder model/ en importeer/.
//   /huis/model/<pad>               -> kluis "web/<pad>"            (index.html = het webmodel)
//   /huis/importeer/                -> kluis "importer/index.html"
//   /huis/importeer/meubels/<pad>   -> kluis "project/Meubels/<pad>" (vers via de GitHub-API als er een token is)
//   /huis/importeer/huis/...        -> doorverwijzen naar /huis/model/...
// Al het andere (deze map zelf, CDN's, de GitHub-API) gaat gewoon door.
importScripts('slot.js');

const BASIS = new URL('./', self.registration.scope).pathname;     // "/huis/"
const SOORTEN = {
  html: 'text/html; charset=utf-8', js: 'text/javascript; charset=utf-8', mjs: 'text/javascript; charset=utf-8',
  css: 'text/css; charset=utf-8', json: 'application/json; charset=utf-8', txt: 'text/plain; charset=utf-8',
  jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', gif: 'image/gif', svg: 'image/svg+xml',
  glb: 'model/gltf-binary', gltf: 'model/gltf+json', bin: 'application/octet-stream',
};

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

let sleutels = null;
self.addEventListener('message', (e) => { if (e.data === 'slot-gewijzigd') { sleutels = null; gecontroleerd = null; } });

function vertaal(url) {
  if (url.origin !== self.location.origin || !url.pathname.startsWith(BASIS)) return null;
  const rest = decodeURIComponent(url.pathname.slice(BASIS.length));
  if (rest === 'model' || rest === 'importeer') return { naar: BASIS + rest + '/' };   // relatieve paden kloppen pas met slash
  if (rest === 'model' || rest.startsWith('model/')) {
    const r = rest.slice(6);
    return { pad: 'web/' + (r === '' || r === 'index.html' ? 'index.html' : r) };
  }
  if (rest === 'importeer' || rest.startsWith('importeer/')) {
    const r = rest.slice(10);
    if (r === '' || r === 'index.html') return { pad: 'importer/index.html' };
    if (r.startsWith('meubels/')) return { pad: 'project/Meubels/' + r.slice(8), vers: true };
    if (r === 'huis' || r.startsWith('huis/')) return { naar: BASIS + 'model/' + r.slice(5) };
  }
  return null;
}

async function bronBytes(naam, vers) {
  const token = vers ? await HuisSlot.token() : null;
  const haal = () => token
    ? fetch(`${HuisSlot.apiBasis()}/repos/${HuisSlot.REPO}/contents/huis/kluis/${naam}?ref=${HuisSlot.TAK}`,
      { headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github.raw' }, cache: 'no-store' })
    : fetch(BASIS + 'kluis/' + naam, { cache: 'no-cache' });
  let r;
  try { r = await haal(); } catch (e) { await new Promise((ok) => setTimeout(ok, 800)); r = await haal(); }   // één keer opnieuw
  return r.ok ? r.arrayBuffer() : (r.status === 404 ? null : Promise.reject(new Error('GitHub gaf ' + r.status)));
}

// Na een nieuw wachtwoord heten alle bestanden anders: een oude sleutel vindt dan niets. Klopt hij nog?
let gecontroleerd = null;
async function sleutelPast() {
  if (gecontroleerd !== null) return gecontroleerd;
  try {
    const info = await (await fetch(BASIS + 'sleutel.json', { cache: 'no-cache' })).json();
    const c = await HuisSlot.ontsleutel(sleutels, HuisSlot.b64(info.controle), 'controle');
    gecontroleerd = new TextDecoder().decode(c) === 'huis';
  } catch (e) { gecontroleerd = false; }
  return gecontroleerd;
}

async function antwoord(req, doel) {
  if (doel.naar) return Response.redirect(doel.naar, 302);
  const navigatie = req.mode === 'navigate';
  sleutels = sleutels || (await HuisSlot.sleutels());
  if (!sleutels) {
    if (navigatie) return Response.redirect(BASIS + '?naar=' + encodeURIComponent(new URL(req.url).pathname), 302);
    return new Response('Vergrendeld', { status: 401 });
  }
  try {
    const blob = await bronBytes(await HuisSlot.naam(sleutels, doel.pad), doel.vers);
    if (!blob && !(await sleutelPast())) throw Object.assign(new Error('andere sleutel'), { name: 'OperationError' });
    if (!blob) return new Response('Niet gevonden: ' + doel.pad, { status: 404, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
    const data = await HuisSlot.ontsleutel(sleutels, blob, doel.pad);
    const ext = doel.pad.split('.').pop().toLowerCase();
    return new Response(data, { headers: { 'Content-Type': SOORTEN[ext] || 'application/octet-stream', 'Cache-Control': 'no-store' } });
  } catch (err) {
    if (err && err.name === 'OperationError') {          // andere sleutel: het wachtwoord is veranderd
      sleutels = null; gecontroleerd = null;
      if (navigatie) return Response.redirect(BASIS + '?fout=sleutel&naar=' + encodeURIComponent(new URL(req.url).pathname), 302);
      return new Response('Sleutel past niet', { status: 403 });
    }
    return new Response(String(err && err.message || err), { status: 502, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  }
}

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const doel = vertaal(new URL(e.request.url));
  if (doel) e.respondWith(antwoord(e.request, doel));
});
