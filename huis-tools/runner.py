"""Eén importer-actie in GitHub Actions (.github/workflows/huis-importer.yml).

  runner.py voorbereiden   importer-code uit de kluis naar $HUIS_WERK/HuisGame/Tools/meubels (+ hash voor de cache)
  runner.py uitvoeren      het verzoek ($HUIS_VERZOEK, versleuteld) uitvoeren met de echte server.py/importeer.py

De data komt uit huis/kluis/ (main) in dezelfde mappen als in Huis inspo (HuisGame/Meubels,
HuisGame/Tools/web-export/meubels), zodat importeer.py niets anders hoeft te weten. Wat veranderde gaat versleuteld
terug naar main (bij een tussentijdse push van iemand anders: opnieuw vanaf de nieuwe stand). Concepten, voorbeelden
en het antwoord gaan naar de tak huis-werk (w/<datum>/<naam>), waar de importpagina ze ophaalt.

Niets uit het verzoek of de importer komt in de (openbare) log: uitvoer en fouten zitten versleuteld in het antwoord.
"""
import base64
import contextlib
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import traceback
import types
import urllib.request
import uuid

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import kluis  # noqa: E402

REPO = os.environ.get("GITHUB_WORKSPACE") or os.path.dirname(HIER)
WERK = os.environ.get("HUIS_WERK") or os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "huis")
CODE = os.path.join(WERK, "HuisGame", "Tools", "meubels")
DATA = {"project/Meubels/": os.path.join(WERK, "HuisGame", "Meubels"),
        "web/meubels/": os.path.join(WERK, "HuisGame", "Tools", "web-export", "meubels")}
WERKTAK = "huis-werk"
CODEBESTANDEN = ("importeer.py", "server.py", "ontdraco.mjs", "optimaliseer.mjs", "package.json", "package-lock.json",
                 "requirements.txt")


def git(*args, check=True, invoer=None, env=None):
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, input=invoer,
                       env=dict(os.environ, **(env or {})))
    if check and r.returncode:
        raise RuntimeError("git %s: %s" % (args[0], r.stderr.decode(errors="replace").strip()[-300:]))
    return r


def de_kluis():
    if not os.environ.get("HUIS_SLEUTEL"):
        raise SystemExit("Geen geheim HUIS_SLEUTEL in deze repo (GitHub → Settings → Secrets and variables → Actions).")
    return kluis.Kluis(os.environ["HUIS_SLEUTEL"], os.path.join(REPO, "huis", "kluis"))


# ---------- voorbereiden ----------

def voorbereiden():
    k = de_kluis()
    info = json.load(open(os.path.join(REPO, "huis", "sleutel.json")))
    if not kluis.controleer(base64.b64decode(os.environ["HUIS_SLEUTEL"]), info):
        raise SystemExit("Het geheim HUIS_SLEUTEL past niet bij huis/sleutel.json. Nieuw wachtwoord gekozen? "
                         "Zet de sleutel die sync.sh wachtwoord gaf als HUIS_SLEUTEL bij GitHub → Settings → Secrets.")
    os.makedirs(CODE, exist_ok=True)
    h = hashlib.sha256(sys.version.encode())
    for n in CODEBESTANDEN:
        data = k.lees("code/" + n)
        if data is None:
            raise SystemExit("De kluis heeft nog geen importer-code (eerst huis-tools/sync.sh draaien).")
        with open(os.path.join(CODE, n), "wb") as f:
            f.write(data)
        if n in ("requirements.txt", "package-lock.json"):
            h.update(data)
    uit = os.environ.get("GITHUB_OUTPUT")
    if uit:
        with open(uit, "a") as f:
            f.write("hash=%s\ncode=%s\n" % (h.hexdigest()[:20], CODE))
    print("importer-code klaar")


# ---------- de tak huis-werk ----------

class Werk:
    """Bestanden op de tak huis-werk: w/<datum>/<naam>, versleuteld met het pad als associated data.
    Schrijven maakt steeds één nieuwe commit zonder ouder (oude dagen eruit), met force-with-lease."""

    def __init__(self, k):
        self.k, self.nieuw, self.geladen = k, {}, False

    def _laad(self):
        if not self.geladen:
            git("fetch", "-q", "--depth=1", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (WERKTAK, WERKTAK), check=False)
            self.geladen = True

    def lees(self, ref):
        if not (isinstance(ref, str) and ref.startswith("w/") and ".." not in ref):
            raise ValueError("ongeldige verwijzing")
        self._laad()
        r = git("show", "origin/%s:%s" % (WERKTAK, ref), check=False)
        if r.returncode:
            raise FileNotFoundError("Het concept is verlopen of weg; begin opnieuw.")
        return self.k.ontsleutel(r.stdout, ref)

    def zet(self, data, datum, naam=None):
        ref = "w/%s/%s" % (datum, naam or uuid.uuid4().hex)
        self.nieuw[ref] = self.k.versleutel(data, ref)
        return ref

    def schrijf(self):
        if not self.nieuw:
            return
        grens = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
        for _ in range(6):
            git("fetch", "-q", "--depth=1", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (WERKTAK, WERKTAK), check=False)
            oud = git("rev-parse", "-q", "--verify", "refs/remotes/origin/" + WERKTAK, check=False).stdout.decode().strip()
            idx = os.path.join(WERK, "werk.index")
            env = {"GIT_INDEX_FILE": idx}
            if os.path.exists(idx):
                os.remove(idx)
            if oud:
                git("read-tree", oud, env=env)
                for regel in git("ls-files", env=env).stdout.decode().splitlines():
                    d = regel.split("/")
                    if len(d) > 1 and d[1] < grens:
                        git("rm", "-q", "--cached", regel, env=env)
            for ref, blob in self.nieuw.items():
                sha = git("hash-object", "-w", "--stdin", invoer=blob).stdout.decode().strip()
                git("update-index", "--add", "--cacheinfo", "100644,%s,%s" % (sha, ref), env=env)
            boom = git("write-tree", env=env).stdout.decode().strip()
            commit = git("commit-tree", boom, "-m", "werk", invoer=b"",
                         env={"GIT_AUTHOR_NAME": "huis", "GIT_AUTHOR_EMAIL": "huis@invalid",
                              "GIT_COMMITTER_NAME": "huis", "GIT_COMMITTER_EMAIL": "huis@invalid"}).stdout.decode().strip()
            r = git("push", "-q", "--force-with-lease=refs/heads/%s:%s" % (WERKTAK, oud), "origin",
                    "%s:refs/heads/%s" % (commit, WERKTAK), check=False)
            if r.returncode == 0:
                self.nieuw = {}
                return
        raise RuntimeError("Kon niet naar %s schrijven." % WERKTAK)


# ---------- data uit en in de kluis ----------

def overslaan(naam):
    return naam.startswith(".") or naam == "__pycache__" or naam.endswith((".tmp", ".pyc"))


def momentopname():
    uit = {}
    for pref, map_ in DATA.items():
        for dirpath, dirs, namen in os.walk(map_):
            dirs[:] = [d for d in dirs if not overslaan(d)]
            for n in namen:
                if not overslaan(n):
                    p = os.path.join(dirpath, n)
                    with open(p, "rb") as f:
                        uit[pref + os.path.relpath(p, map_).replace(os.sep, "/")] = kluis.sha(f.read())
    return uit


def zet_data_klaar(k, idx):
    for map_ in DATA.values():
        shutil.rmtree(map_, ignore_errors=True)
        os.makedirs(map_)
    for pad in idx:
        for pref, map_ in DATA.items():
            if pad.startswith(pref):
                doel = os.path.join(map_, pad[len(pref):])
                os.makedirs(os.path.dirname(doel), exist_ok=True)
                with open(doel, "wb") as f:
                    f.write(k.lees(pad))
    os.makedirs(os.path.join(DATA["project/Meubels/"], ".concept"), exist_ok=True)


def wijzigingen(voor, na):
    uit = {}
    for pad in set(voor) | set(na):
        if voor.get(pad) != na.get(pad):
            if pad in na:
                pref = next(p for p in DATA if pad.startswith(p))
                with open(os.path.join(DATA[pref], pad[len(pref):]), "rb") as f:
                    uit[pad] = f.read()
            else:
                uit[pad] = None
    return uit


def vraag_pages_build():
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        return
    try:
        urllib.request.urlopen(urllib.request.Request(
            "https://api.github.com/repos/%s/pages/builds" % repo, method="POST", data=b"",
            headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"}), timeout=20)
    except Exception:
        pass                                      # de push zelf start meestal ook een build


# ---------- de importer ----------

class MeteenThread:
    """server.py start lange stappen in een thread; hier doen we ze meteen (de Action wacht toch)."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.f, self.a, self.kw = target, args, kwargs or {}

    def start(self):
        self.f(*self.a, **self.kw)


def laad_importer():
    sys.path.insert(0, CODE)
    import server                                 # noqa: E402  (importeert ook importeer als server.imp)
    server.threading = types.SimpleNamespace(Thread=MeteenThread, Lock=server.threading.Lock)
    return server


class Acties:
    def __init__(self, server, werk, datum):
        self.s, self.imp, self.werk, self.datum = server, server.imp, werk, datum
        self.h = server.Handler.__new__(server.Handler)    # zonder socket: alleen de api_*-methoden

    def concept(self, taak):
        """Toestand van een taak, met de conceptbestanden op huis-werk (de pagina rekent zelf verder met raw.glb)."""
        t = self.s.TAKEN[taak]
        uit = {"taak": taak, "status": t["status"], "log": t["log"], "fout": t.get("fout"), "info": t["info"], "id": t["id"]}
        if t["status"] == "klaar":
            r = self.s._resultaat(taak)
            bestanden = {}
            for dirpath, _, namen in os.walk(t["map"]):
                for n in namen:
                    p = os.path.join(dirpath, n)
                    with open(p, "rb") as f:
                        bestanden[os.path.relpath(p, t["map"]).replace(os.sep, "/")] = self.werk.zet(f.read(), self.datum)
            r["glb"] = bestanden.get("model.glb")
            uit.update(resultaat=r, bestanden=bestanden)
        return uit

    def zet_concept_terug(self, b, status="klaar"):
        taak = b.get("taak") or uuid.uuid4().hex[:10]
        map_ = os.path.join(self.s.CONCEPT, taak)
        shutil.rmtree(map_, ignore_errors=True)
        for rel, ref in (b.get("bestanden") or {}).items():
            p = os.path.normpath(os.path.join(map_, rel))
            if not p.startswith(map_ + os.sep):
                raise self.s.Fout("ongeldig pad")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(self.werk.lees(ref))
        os.makedirs(map_, exist_ok=True)
        info = b["info"]
        self.s.TAKEN[taak] = {"status": status, "log": [], "info": info, "id": b.get("id") or self.imp.meubel_id(info),
                              "map": map_, "draai": int(b.get("draai") or 0) % 360, "schaal": bool(b.get("schaal")),
                              "kantel": bool(b.get("kantel")), "eenheid": b.get("eenheid") or "auto", "versie": 0,
                              "herkomst": b.get("herkomst")}
        return taak

    # -- met een concept --

    def lees(self, b):
        uit = self.h.api_lees(b)
        if uit["info"].get("modellen"):           # alvast ophalen: scheelt een hele Action-ronde
            try:
                uit["voorraad"] = self.maak({"url": b["url"], "info": uit["info"]})
            except Exception as e:
                uit["voorraad"] = {"status": "fout", "fout": str(e), "log": []}
        return uit

    def maak(self, b):
        self.s.PAGINA_INFO[b["url"]] = b["info"]
        taak = self.h.api_maak(b)["taak"]         # loopt meteen helemaal (MeteenThread)
        return self.concept(taak)

    def eigen(self, b):
        r = self.h.api_eigen({"naam": b.get("naam"), "maten": b.get("maten")})
        t = self.s.TAKEN[r["taak"]]
        totaal = 0
        for x in b.get("bestanden") or []:
            rel = os.path.normpath(str(x["pad"]).replace("\\", "/")).lstrip("/")
            if not rel or rel.startswith("..") or not rel.lower().endswith(self.imp.LOS_TOEGESTAAN):
                raise self.s.Fout("Dit soort bestand wordt niet gebruikt: %s" % os.path.basename(rel))
            data = self.werk.lees(x["ref"])
            totaal += len(data)
            if totaal > 4 * self.imp.MAX_DOWNLOAD:
                raise self.s.Fout("Samen groter dan %d MB." % (4 * self.imp.MAX_DOWNLOAD >> 20))
            uit = self.h._binnen(os.path.join(t["map"], "bron"), rel)
            os.makedirs(os.path.dirname(uit), exist_ok=True)
            with open(uit, "wb") as f:
                f.write(data)
        self.h.api_eigen_klaar({"taak": r["taak"]})
        return self.concept(r["taak"])

    def bewaar(self, b):
        taak = self.zet_concept_terug(b)
        self.s._normaliseer(taak)                 # met de draai, schaal, kanteling en eenheid van de pagina
        return self.h.api_bewaar(dict(b, taak=taak))

    # -- verven: de pagina geeft de gelezen info mee --

    def verf_kleur(self, b):
        self.s.PAGINA_INFO[b["url"]] = b["info"]
        return self.h.api_verf_kleur(b)

    def verf_bewaar(self, b):
        self.s.PAGINA_INFO[b["url"]] = b["info"]
        return self.h.api_verf_bewaar(b)

    # -- kleiner maken: het voorbeeld gaat naar huis-werk --

    def optimaliseer(self, b):
        uit = self.h.api_optimaliseer(b)
        uit["glb"] = self.werk.zet(self.s.VOORBEELDEN[b["id"]]["glb"], self.datum)
        return uit

    def __getattr__(self, naam):                  # de rest: precies zoals server.py
        f = getattr(self.h, "api_" + naam, None)
        if not f or naam.startswith("_"):
            raise self.s.Fout("onbekende actie", 404)
        return f


TOEGESTAAN = {"lees", "maak", "eigen", "bewaar", "hitboxes_auto", "hitboxes", "anker", "categorie", "categorie_raad",
              "verf_lees", "verf_kleur", "verf_bewaar", "verf_verwijder", "optimaliseer", "optimaliseer_bewaar",
              "huismodel", "verwijder"}


def voer_uit(verzoek, k, werk):
    actie, body, datum = verzoek["actie"], verzoek.get("body") or {}, verzoek["datum"]
    if actie not in TOEGESTAAN:
        raise ValueError("onbekende actie: %s" % actie)
    server = laad_importer()
    for poging in range(5):
        git("fetch", "-q", "--depth=1", "origin", "main")
        git("reset", "-q", "--hard", "FETCH_HEAD")
        k = de_kluis()
        idx = k.index()
        zet_data_klaar(k, idx)
        for d in (server.TAKEN, server.PAGINA_INFO, server.VOORBEELDEN):
            d.clear()
        werk.nieuw = {}
        voor = momentopname()
        try:
            antwoord = getattr(Acties(server, werk, datum), actie)(body)
        except server.Fout as e:
            return {"fout": str(e), "code": e.code}
        nieuw = wijzigingen(voor, momentopname())
        if not nieuw:
            return antwoord
        k.werk_bij(nieuw, oud=idx)
        git("add", "-A", "huis/kluis")
        git("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-qm", "Huis bijgewerkt via de importer")
        if git("push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
            vraag_pages_build()
            antwoord["_commit"] = git("rev-parse", "HEAD").stdout.decode().strip()
            return antwoord
    raise RuntimeError("Kon de wijziging niet op main zetten (te veel tegelijk).")


def uitvoeren():
    k = de_kluis()
    werk = Werk(k)
    ruw = os.environ.get("HUIS_VERZOEK") or ""
    if ruw.startswith("w/"):                      # groot verzoek: staat zelf op huis-werk
        verzoek = json.loads(werk.lees(ruw))
    else:
        verzoek = json.loads(k.ontsleutel(base64.b64decode(ruw), "verzoek"))
    if verzoek.get("id") != os.environ.get("HUIS_ID"):
        raise SystemExit("verzoek hoort niet bij deze run")
    uitvoer = io.StringIO()
    try:
        with contextlib.redirect_stdout(uitvoer), contextlib.redirect_stderr(uitvoer):
            antwoord = voer_uit(verzoek, k, werk)
    except BaseException as e:                    # ook SystemExit uit importeer.py
        traceback.print_exc(file=uitvoer)
        antwoord = {"fout": str(e) or e.__class__.__name__, "code": 500}
    antwoord["_uitvoer"] = uitvoer.getvalue()[-20000:]
    werk.zet(json.dumps(antwoord, ensure_ascii=False, default=str).encode(), verzoek["datum"], verzoek["id"])
    werk.schrijf()
    print("klaar" if "fout" not in antwoord else "klaar, met een melding voor de importpagina")


if __name__ == "__main__":
    {"voorbereiden": voorbereiden, "uitvoeren": uitvoeren}[sys.argv[1]]()
