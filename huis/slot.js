// Het slot van het huis: sleutel uit het wachtwoord, ontsleutelen van huis/kluis/, GitHub-token.
// Zelfde formaat als huis-tools/kluis.py. Klassiek script: werkt in de pagina (window) en in de service worker (self).
(function (wereld) {
  'use strict';
  const REPO = 'vanGenderingen/vanGenderingen.github.io';
  const TAK = 'main';
  const WERKTAK = 'huis-werk';
  const MAGIC = [0x48, 0x4b, 0x31];   // "HK1"
  const tekst = new TextEncoder();

  // Lokaal testen (huis-tools/test/nep_github.py) gaat tegen een nep-GitHub op dezelfde server.
  function apiBasis() {
    const h = wereld.location.hostname;
    return h === 'localhost' || h === '127.0.0.1' ? wereld.location.origin + '/_api' : 'https://api.github.com';
  }

  /* ---------- IndexedDB: sleutels en token blijven op dit apparaat ---------- */

  function db() {
    return new Promise((ok, nee) => {
      const r = indexedDB.open('huis-slot', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('dingen');
      r.onsuccess = () => ok(r.result);
      r.onerror = () => nee(r.error);
    });
  }
  async function bewaar(naam, waarde) {
    const d = await db();
    await new Promise((ok, nee) => {
      const t = d.transaction('dingen', 'readwrite');
      if (waarde === undefined) t.objectStore('dingen').delete(naam); else t.objectStore('dingen').put(waarde, naam);
      t.oncomplete = ok; t.onerror = () => nee(t.error);
    });
    d.close();
  }
  async function haal(naam) {
    const d = await db();
    const uit = await new Promise((ok, nee) => {
      const r = d.transaction('dingen').objectStore('dingen').get(naam);
      r.onsuccess = () => ok(r.result); r.onerror = () => nee(r.error);
    });
    d.close();
    return uit;
  }

  /* ---------- sleutel ---------- */

  const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const naarB64 = (bytes) => {
    let s = ''; const b = new Uint8Array(bytes);
    for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
    return btoa(s);
  };

  async function uitBytes(bits) {
    const aes = await crypto.subtle.importKey('raw', bits.slice(0, 32), 'AES-GCM', false, ['encrypt', 'decrypt']);
    const mac = await crypto.subtle.importKey('raw', bits.slice(32, 64), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    return { aes, mac };
  }

  // Wachtwoord + huis/sleutel.json -> sleutels, of null als het wachtwoord niet klopt.
  async function uitWachtwoord(wachtwoord, info) {
    const basis = await crypto.subtle.importKey('raw', tekst.encode(wachtwoord), 'PBKDF2', false, ['deriveBits']);
    const bits = new Uint8Array(await crypto.subtle.deriveBits(
      { name: 'PBKDF2', hash: 'SHA-256', salt: b64(info.zout), iterations: info.iteraties }, basis, 512));
    const s = await uitBytes(bits);
    try {
      const c = new TextDecoder().decode(await ontsleutel(s, b64(info.controle), 'controle'));
      return c === 'huis' ? s : null;
    } catch (e) { return null; }
  }

  async function naam(s, pad) {
    const h = new Uint8Array(await crypto.subtle.sign('HMAC', s.mac, tekst.encode(pad)));
    return Array.from(h.subarray(0, 16), (x) => x.toString(16).padStart(2, '0')).join('');
  }

  async function ontsleutel(s, blob, aad) {
    const b = new Uint8Array(blob);
    if (b[0] !== MAGIC[0] || b[1] !== MAGIC[1] || b[2] !== MAGIC[2]) throw new Error('geen kluisbestand: ' + aad);
    return crypto.subtle.decrypt({ name: 'AES-GCM', iv: b.subarray(3, 15), additionalData: tekst.encode(aad) }, s.aes, b.subarray(15));
  }

  async function versleutel(s, data, aad) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: tekst.encode(aad) }, s.aes, data));
    const uit = new Uint8Array(15 + ct.length);
    uit.set(MAGIC, 0); uit.set(iv, 3); uit.set(ct, 15);
    return uit;
  }

  wereld.HuisSlot = {
    REPO, TAK, WERKTAK, apiBasis,
    uitWachtwoord, naam, ontsleutel, versleutel, b64, naarB64,
    sleutels: () => haal('sleutels'),
    bewaarSleutels: (s) => bewaar('sleutels', s),
    vergeet: async () => { await bewaar('sleutels', undefined); },
    token: () => haal('token'),
    bewaarToken: (t) => bewaar('token', t || undefined),
  };
})(typeof self !== 'undefined' ? self : window);
