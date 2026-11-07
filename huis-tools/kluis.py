"""De kluis: versleutelde bestanden van het huis in huis/kluis/ (openbare repo, dus alles AES-256-GCM).

Hetzelfde formaat als huis/slot.js in de browser:
- sleutel: 64 bytes = PBKDF2-HMAC-SHA256(wachtwoord, zout, iteraties); de eerste 32 voor AES-GCM, de laatste 32 voor HMAC.
- bestandsnaam: huis/kluis/<eerste 16 bytes van HMAC-SHA256(mac, pad) als hex>, zodat ook de namen niets verraden.
- inhoud: b"HK1" + iv (12 bytes) + AES-GCM(data) met het logische pad als associated data (bestanden zijn niet te verwisselen).
- huis/kluis/<naam("_index")>: {"versie": 1, "bestanden": {pad: {"sha": sha256 van de platte inhoud, "grootte": n}}}.

Logische paden: web/... (webmodel, meubels, foto's), importer/index.html, code/... (importer-code),
project/Meubels/... (de meubelcatalogus), importer.json, _sync/basis (stand van de laatste sync).
Alleen standaard-Python plus `cryptography`.
"""
import base64
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"HK1"
INDEX = "_index"
BASIS = "_sync/basis"
ITERATIES = 600_000


def leid_af(wachtwoord, zout, iteraties=ITERATIES):
    return hashlib.pbkdf2_hmac("sha256", wachtwoord.encode("utf-8"), zout, iteraties, dklen=64)


class Kluis:
    def __init__(self, sleutel, map_):
        """sleutel: 64 bytes (of base64 daarvan); map_: de map huis/kluis in de repo."""
        if isinstance(sleutel, str):
            sleutel = base64.b64decode(sleutel.strip())
        if len(sleutel) != 64:
            raise ValueError("De sleutel moet 64 bytes zijn.")
        self.aes = AESGCM(sleutel[:32])
        self.mac = sleutel[32:]
        self.map = map_

    # ---------- losse blobs ----------

    def versleutel(self, data, aad):
        iv = os.urandom(12)
        return MAGIC + iv + self.aes.encrypt(iv, bytes(data), aad.encode("utf-8"))

    def ontsleutel(self, blob, aad):
        if blob[:3] != MAGIC:
            raise ValueError("Geen kluisbestand: %s" % aad)
        return self.aes.decrypt(blob[3:15], blob[15:], aad.encode("utf-8"))

    # ---------- bestanden op logisch pad ----------

    def naam(self, pad):
        return hmac.new(self.mac, pad.encode("utf-8"), hashlib.sha256).digest()[:16].hex()

    def bestand(self, pad):
        return os.path.join(self.map, self.naam(pad))

    def lees(self, pad):
        p = self.bestand(pad)
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return self.ontsleutel(f.read(), pad)

    def schrijf(self, pad, data):
        os.makedirs(self.map, exist_ok=True)
        with open(self.bestand(pad), "wb") as f:
            f.write(self.versleutel(data, pad))

    def verwijder(self, pad):
        p = self.bestand(pad)
        if os.path.isfile(p):
            os.remove(p)

    def lees_json(self, pad, standaard=None):
        data = self.lees(pad)
        return json.loads(data) if data is not None else standaard

    def schrijf_json(self, pad, waarde):
        self.schrijf(pad, json.dumps(waarde, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))

    # ---------- index ----------

    def index(self):
        return (self.lees_json(INDEX) or {}).get("bestanden", {})

    def bewaar_index(self, bestanden):
        self.schrijf_json(INDEX, {"versie": 1, "bestanden": bestanden})

    def werk_bij(self, nieuw, oud=None):
        """Zet de platte bestanden in nieuw ({pad: bytes of None = weg}) in de kluis en werk de index bij.
        Alleen wat echt anders is wordt opnieuw versleuteld (elke versleuteling geeft andere bytes). Geeft de
        lijst gewijzigde paden."""
        idx = dict(self.index() if oud is None else oud)
        veranderd = []
        for pad, data in sorted(nieuw.items()):
            if data is None:
                if pad in idx:
                    self.verwijder(pad)
                    del idx[pad]
                    veranderd.append(pad)
                continue
            sha = hashlib.sha256(data).hexdigest()
            if idx.get(pad, {}).get("sha") == sha and os.path.isfile(self.bestand(pad)):
                continue
            self.schrijf(pad, data)
            idx[pad] = {"sha": sha, "grootte": len(data)}
            veranderd.append(pad)
        if veranderd:
            self.bewaar_index(idx)
        return veranderd


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sleutelbestand(sleutel, zout, iteraties=ITERATIES):
    """Inhoud van huis/sleutel.json: alles wat de browser nodig heeft om het wachtwoord te controleren."""
    k = Kluis(sleutel, "")
    return {"versie": 1, "kdf": "PBKDF2-SHA256", "iteraties": iteraties,
            "zout": base64.b64encode(zout).decode(),
            "controle": base64.b64encode(k.versleutel(b"huis", "controle")).decode()}


def controleer(sleutel, info):
    try:
        return Kluis(sleutel, "").ontsleutel(base64.b64decode(info["controle"]), "controle") == b"huis"
    except Exception:
        return False
