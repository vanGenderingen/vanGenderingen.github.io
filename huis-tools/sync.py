"""Huis inspo  <->  vangenderingen.nl/huis/  (versleuteld in huis/kluis/ van deze repo).

  huis-tools/sync.sh init           wachtwoord kiezen (eerste keer), of op een nieuwe Mac de sleutel ophalen
  huis-tools/sync.sh                laten zien wat er gebeurt, vragen, en synchroniseren
  huis-tools/sync.sh --droog        alleen laten zien
  huis-tools/sync.sh --ja           zonder vragen
  huis-tools/sync.sh --neem site    bij een conflict de versie van de site houden (of: --neem lokaal)
  huis-tools/sync.sh wachtwoord     nieuw wachtwoord: alles opnieuw versleutelen

Wat gaat waarheen:
  alleen naar de site    webmodel (vergierdeweg-274.html), importpagina en importer-code uit Huis inspo **main**,
                         foto's en inspo (web-export/fotos, inspo)
  twee kanten op         web-export/meubels/  <->  web/meubels/      (base64-meubels + catalogus van het webmodel)
                         HuisGame/Meubels/    <->  project/Meubels/  (de catalogus van de importer)
Wat op de site geïmporteerd is komt zo terug naar Huis inspo; wat je lokaal importeerde gaat de site op. Is hetzelfde
bestand aan beide kanten veranderd, dan stopt het script (catalogi worden per meubel samengevoegd). Wat het lokaal
vervangt of weghaalt, komt eerst in ~/Library/Application Support/huis-site/vervangen/<tijd>/.

Het werkt in een eigen clone (~/Library/Caches/huis-site/repo), niet in je checkout van deze repo.
"""
import argparse
import ast
import base64
import datetime
import getpass
import json
import os
import shutil
import subprocess
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)                       # -I zet de scriptmap niet op het pad
import kluis  # noqa: E402

REPO_URL = os.environ.get("HUIS_SITE_REPO", "https://github.com/vanGenderingen/vanGenderingen.github.io.git")
TAK = "main"
BRON_TAK = os.environ.get("HUIS_INSPO_TAK", "main")      # alleen om te testen
BRON = os.path.expanduser(os.environ.get("HUIS_INSPO", "~/Documents/Repos/Huis inspo"))
STEUN = os.path.expanduser(os.environ.get("HUIS_SITE_MAP", "~/Library/Application Support/huis-site"))
SLEUTEL = os.path.expanduser(os.environ.get("HUIS_SITE_SLEUTEL", "~/.config/huis/site-sleutel"))
CLONE = os.path.expanduser(os.environ.get("HUIS_SITE_CLONE", "~/Library/Caches/huis-site/repo"))

# kluispad -> pad in Huis inspo (git main), alleen naar de site
UIT_GIT = {
    "web/index.html": "HuisGame/Tools/web-export/vergierdeweg-274.html",
    "importer/index.html": "HuisGame/Tools/meubels/importeer.html",
    "code/importeer.py": "HuisGame/Tools/meubels/importeer.py",
    "code/server.py": "HuisGame/Tools/meubels/server.py",
    "code/ontdraco.mjs": "HuisGame/Tools/meubels/ontdraco.mjs",
    "code/optimaliseer.mjs": "HuisGame/Tools/meubels/optimaliseer.mjs",
    "code/package.json": "HuisGame/Tools/meubels/package.json",
    "code/package-lock.json": "HuisGame/Tools/meubels/package-lock.json",
}
EENWEG_MAPPEN = [("web/fotos/", "HuisGame/Tools/web-export/fotos/"), ("web/inspo/", "HuisGame/Tools/web-export/inspo/")]
TWEEWEG = [("web/meubels/", "HuisGame/Tools/web-export/meubels/"), ("project/Meubels/", "HuisGame/Meubels/")]
GEMAAKT = ("code/requirements.txt", "importer.json")
JSON_LIJSTEN = {"project/Meubels/catalogus.json": ("meubels",), "project/Meubels/verven.json": ("verven",),
                "web/meubels/catalogus.json": ("meubels", "verven")}
VENV_PY = "HuisGame/Tools/meubels/.venv/bin/python"

# De importpagina praat op de site met GitHub in plaats van met server.py (zie huis/github.js).
INJECTIE = '<script src="/huis/slot.js"></script>\n<script type="module" src="/huis/github.js"></script>\n'


def zeg(*a):
    print(*a, flush=True)


def stop(tekst, code=1):
    print("\n" + tekst, file=sys.stderr)
    sys.exit(code)


def git(*args, cwd=CLONE, check=True, tekst=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=tekst)
    if check and r.returncode:
        stop("git %s mislukte:\n%s" % (" ".join(args), (r.stderr or r.stdout)))
    return r


def mb(n):
    return "%.1f MB" % (n / 1e6) if n >= 1e5 else "%d kB" % max(1, round(n / 1e3))


# ---------- sleutel en clone ----------

def lees_sleutel():
    if not os.path.isfile(SLEUTEL):
        stop("Geen sleutel op deze Mac. Draai eerst: huis-tools/sync.sh init")
    return open(SLEUTEL).read().strip()


def bewaar_sleutel(sleutel):
    os.makedirs(os.path.dirname(SLEUTEL), exist_ok=True)
    fd = os.open(SLEUTEL, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(sleutel).decode() + "\n")


def vers_clone():
    """De eigen clone gelijk aan origin/main (lokale wijzigingen daarin zijn altijd van een mislukte vorige poging)."""
    if not os.path.isdir(os.path.join(CLONE, ".git")):
        zeg("Eigen clone maken in %s …" % CLONE)
        os.makedirs(os.path.dirname(CLONE), exist_ok=True)
        git("clone", "--branch", TAK, REPO_URL, CLONE, cwd=os.path.dirname(CLONE))
    git("fetch", "origin", TAK)
    git("checkout", "-q", TAK)
    git("reset", "-q", "--hard", "origin/" + TAK)
    git("clean", "-qfd", "huis")


def push(bericht):
    git("add", "-A", "huis")
    if not git("status", "--porcelain", "huis").stdout.strip():
        return "niets"
    git("-c", "user.name=%s" % (git("config", "user.name", check=False).stdout.strip() or "Huis sync"),
        "commit", "-qm", bericht)
    r = git("push", "-q", "origin", TAK, check=False)
    return "ok" if r.returncode == 0 else "afgewezen"


# ---------- init en wachtwoord ----------

def vraag_wachtwoord(nieuw):
    if os.environ.get("HUIS_WACHTWOORD"):           # voor scripts en tests; anders vragen
        return os.environ["HUIS_WACHTWOORD"]
    while True:
        w = getpass.getpass("Nieuw wachtwoord: " if nieuw else "Wachtwoord: ")
        if not nieuw:
            return w
        if len(w) < 10:
            zeg("Kies er een van minstens 10 tekens (het beschermt een openbare repo).")
            continue
        if getpass.getpass("Nog een keer: ") != w:
            zeg("Die waren niet gelijk.")
            continue
        return w


def secret_uitleg(sleutel):
    zeg("\nZet deze sleutel als geheim in GitHub, zodat de importer-Action de kluis kan openen:")
    zeg("  github.com/vanGenderingen/vanGenderingen.github.io → Settings → Secrets and variables → Actions")
    zeg("  → New repository secret, naam: HUIS_SLEUTEL, waarde:\n")
    zeg("  " + base64.b64encode(sleutel).decode() + "\n")
    zeg("(Hij staat ook in %s.)" % SLEUTEL)


def cmd_init(_):
    vers_clone()
    pad = os.path.join(CLONE, "huis", "sleutel.json")
    if os.path.isfile(pad):
        info = json.load(open(pad))
        zeg("Het huis heeft al een wachtwoord. Vul het in om de sleutel op deze Mac te bewaren.")
        s = kluis.leid_af(vraag_wachtwoord(False), base64.b64decode(info["zout"]), info["iteraties"])
        if not kluis.controleer(s, info):
            stop("Dat wachtwoord klopt niet.")
        bewaar_sleutel(s)
        zeg("Sleutel bewaard in %s." % SLEUTEL)
        return
    zout = os.urandom(16)
    s = kluis.leid_af(vraag_wachtwoord(True), zout)
    with open(pad, "w") as f:
        json.dump(kluis.sleutelbestand(s, zout), f, indent=1)
    bewaar_sleutel(s)
    if push("Huis: wachtwoord ingesteld") != "ok":
        stop("Pushen mislukte; draai init opnieuw.")
    zeg("Wachtwoord ingesteld en sleutel bewaard.")
    secret_uitleg(s)
    zeg("Zet het huis er daarna op met: huis-tools/sync.sh")


def cmd_wachtwoord(a):
    oud = kluis.Kluis(lees_sleutel(), "")
    vers_clone()
    km = os.path.join(CLONE, "huis", "kluis")
    oud.map = km
    pad = os.path.join(CLONE, "huis", "sleutel.json")
    if not kluis.controleer(base64.b64decode(lees_sleutel()), json.load(open(pad))):
        stop("De sleutel op deze Mac past niet bij de site. Draai eerst init.")
    idx = oud.index()
    inhoud = {p: oud.lees(p) for p in idx}
    basis = oud.lees(kluis.BASIS)
    zout = os.urandom(16)
    s = kluis.leid_af(vraag_wachtwoord(True), zout)
    shutil.rmtree(km, ignore_errors=True)
    nieuw = kluis.Kluis(s, km)
    nieuw.werk_bij(inhoud, oud={})
    if basis is not None:
        nieuw.schrijf(kluis.BASIS, basis)
    with open(pad, "w") as f:
        json.dump(kluis.sleutelbestand(s, zout), f, indent=1)
    if push("Huis: nieuw wachtwoord") != "ok":
        stop("Pushen mislukte (iemand anders werkte tegelijk?). Probeer het opnieuw.")
    bewaar_sleutel(s)
    zeg("Alles opnieuw versleuteld (%d bestanden)." % len(inhoud))
    secret_uitleg(s)
    zeg("Belangrijk: vervang HUIS_SLEUTEL in GitHub, anders kan de importer de kluis niet meer openen.")


# ---------- lokale kant ----------

def overslaan(naam):
    return naam.startswith(".") or naam == "__pycache__" or naam.endswith((".tmp", ".pyc"))


def map_bestanden(kluis_pref, lokaal_pref):
    """{kluispad: lokaal pad} voor alle bestanden in een map van Huis inspo."""
    uit, wortel = {}, os.path.join(BRON, lokaal_pref)
    for dirpath, dirs, namen in os.walk(wortel):
        dirs[:] = sorted(d for d in dirs if not overslaan(d))
        for n in namen:
            if not overslaan(n):
                p = os.path.join(dirpath, n)
                uit[kluis_pref + os.path.relpath(p, wortel).replace(os.sep, "/")] = p
    return uit


def git_bron(pad):
    r = subprocess.run(["git", "show", "%s:%s" % (BRON_TAK, pad)], cwd=BRON, capture_output=True)
    if r.returncode:
        stop("Kon %s niet lezen uit Huis inspo %s:\n%s" % (pad, BRON_TAK, r.stderr.decode()))
    return r.stdout


def importpagina(html):
    t = html.decode("utf-8")
    plek = t.find('<script type="module">')
    if plek < 0:
        stop("De importpagina heeft geen <script type=\"module\"> meer; pas INJECTIE in sync.py aan.")
    if "huisBackend" not in t:
        stop("De importpagina in Huis inspo main kent window.huisBackend nog niet (de aanpassing voor de site ontbreekt).")
    return (t[:plek] + INJECTIE + t[plek:]).encode("utf-8")


def gemaakt(code_py):
    """importer.json (vaste lijsten uit importeer.py) en requirements.txt (de .venv van Huis inspo)."""
    waarden = {}
    for knoop in ast.parse(code_py).body:
        if isinstance(knoop, ast.Assign) and len(knoop.targets) == 1 and isinstance(knoop.targets[0], ast.Name):
            naam = knoop.targets[0].id
            if naam in ("CATEGORIEEN", "_RADEN", "MAX_TRIS", "ANKERS", "MODEL_BESTANDEN", "LOS_TOEGESTAAN", "MAX_DOWNLOAD"):
                try:
                    waarden[naam] = ast.literal_eval(knoop.value)
                except ValueError:
                    pass
    if "LOS_TOEGESTAAN" not in waarden and "MODEL_BESTANDEN" in waarden:
        waarden["LOS_TOEGESTAAN"] = waarden["MODEL_BESTANDEN"]
    info = {"categorieen": list(waarden.get("CATEGORIEEN", ())), "raden": [list(x) for x in waarden.get("_RADEN", ())],
            "max_driehoeken": waarden.get("MAX_TRIS", 150000), "meshy": False}
    py = os.path.join(BRON, VENV_PY)
    if not os.path.isfile(py):
        stop("Geen importer-omgeving in Huis inspo (%s)." % VENV_PY)
    r = subprocess.run([py, "-m", "pip", "freeze", "--exclude-editable"], capture_output=True, text=True)
    if r.returncode:
        stop("pip freeze mislukte: %s" % r.stderr)
    req = "".join(l + "\n" for l in r.stdout.splitlines() if l and not l.startswith(("-e", "#")) and " @ " not in l)
    return {"importer.json": json.dumps(info, ensure_ascii=False, indent=1).encode(), "code/requirements.txt": req.encode()}


def lokaal_eenweg():
    uit = {}
    for kp, bp in UIT_GIT.items():
        uit[kp] = git_bron(bp)
    uit["importer/index.html"] = importpagina(uit["importer/index.html"])
    uit.update(gemaakt(uit["code/importeer.py"]))
    for kp, lp in EENWEG_MAPPEN:
        for k, p in map_bestanden(kp, lp).items():
            uit[k] = open(p, "rb").read()
    return uit


def lokaal_pad(kp):
    for pref, lpref in TWEEWEG:
        if kp.startswith(pref):
            return os.path.join(BRON, lpref, kp[len(pref):])
    raise KeyError(kp)


def waarschuw_werkmap():
    r = subprocess.run(["git", "status", "--porcelain", "--", *UIT_GIT.values()], cwd=BRON, capture_output=True, text=True)
    if r.stdout.strip():
        zeg("Let op: in Huis inspo staan niet-gecommitte wijzigingen aan het webmodel of de importer. De site krijgt de"
            " versie van main:\n  " + r.stdout.strip().replace("\n", "\n  "))


# ---------- samenvoegen ----------

def lijst_samenvoegen(basis, lokaal, site, neem, pad, conflicten):
    """3-weg per id. Volgorde: die van lokaal, met nieuwe van de site erachter."""
    b = {x["id"]: x for x in basis}
    l = {x["id"]: x for x in lokaal}
    s = {x["id"]: x for x in site}
    ids = list(l) + [i for i in s if i not in l]
    uit = []
    for i in ids + [i for i in b if i not in l and i not in s]:
        bl, ll, sl = b.get(i), l.get(i), s.get(i)
        if ll == sl:
            kies = ll
        elif ll == bl:
            kies = sl
        elif sl == bl:
            kies = ll
        elif neem:
            kies = sl if neem == "site" else ll
        else:
            conflicten.append("%s → %s (aan beide kanten anders)" % (pad, i))
            kies = ll
        if kies is not None and kies not in uit:
            uit.append(kies)
    return uit


def json_samenvoegen(pad, basis, lokaal, site, neem, conflicten):
    sleutels = JSON_LIJSTEN[pad]
    uit = dict(site or {})
    uit.update(lokaal or {})
    for k in sleutels:
        uit[k] = lijst_samenvoegen((basis or {}).get(k, []), (lokaal or {}).get(k, []), (site or {}).get(k, []),
                                   neem, pad, conflicten)
    if not uit.get("verven") and "verven" in sleutels and "verven" not in (lokaal or {}) and "verven" not in (site or {}):
        uit.pop("verven", None)
    return (json.dumps(uit, ensure_ascii=False, indent=2)).encode("utf-8")   # zoals importeer._bewaar_json


def plan(k, neem):
    idx = k.index()
    basis = k.lees_json(kluis.BASIS) or {"bestanden": {}, "json": {}}
    bb = basis.get("bestanden", {})
    op_site, naar_lokaal, conflicten = {}, {}, []      # kluispad -> bytes | None

    eenweg = lokaal_eenweg()
    eenweg_pref = tuple(p for p, _ in EENWEG_MAPPEN) + ("code/",)
    for kp, data in eenweg.items():
        if idx.get(kp, {}).get("sha") != kluis.sha(data):
            op_site[kp] = data
    for kp in idx:
        if kp.startswith(eenweg_pref) and kp not in eenweg:
            op_site[kp] = None

    lokaal = {}
    for kpref, lpref in TWEEWEG:
        lokaal.update(map_bestanden(kpref, lpref))
    lsha = {kp: kluis.sha(open(p, "rb").read()) for kp, p in lokaal.items()}
    tweeweg_pref = tuple(p for p, _ in TWEEWEG)
    alle = sorted({p for p in list(lokaal) + list(idx) + list(bb) if p.startswith(tweeweg_pref)})
    for kp in alle:
        b, l, s = bb.get(kp), lsha.get(kp), idx.get(kp, {}).get("sha")
        if l == s:
            continue
        if kp in JSON_LIJSTEN:
            lees = lambda p: json.loads(open(p, "rb").read()) if p else None
            site = k.lees_json(kp) if s else None
            samen = json_samenvoegen(kp, basis.get("json", {}).get(kp), lees(lokaal.get(kp)), site, neem, conflicten)
            if kluis.sha(samen) != l:
                naar_lokaal[kp] = samen
            if kluis.sha(samen) != s:
                op_site[kp] = samen
        elif l == b:
            naar_lokaal[kp] = None if s is None else ("site", kp)
        elif s == b:
            op_site[kp] = None if l is None else open(lokaal[kp], "rb").read()
        elif neem == "site":
            naar_lokaal[kp] = None if s is None else ("site", kp)
        elif neem == "lokaal":
            op_site[kp] = None if l is None else open(lokaal[kp], "rb").read()
        else:
            conflicten.append("%s (lokaal en op de site anders veranderd)" % kp)
    return op_site, naar_lokaal, conflicten, idx


def toon_plan(op_site, naar_lokaal, idx):
    def regels(d, kant):
        for kp, v in sorted(d.items()):
            if v is None:
                zeg("  %s weg     %s" % (kant, kp))
            else:
                n = len(v) if isinstance(v, bytes) else idx[kp]["grootte"]
                zeg("  %s %-6s %s (%s)" % (kant, "nieuw" if (kp not in idx if kant == "→ site " else not os.path.exists(lokaal_pad(kp))) else "anders", kp, mb(n)))
    if not op_site and not naar_lokaal:
        zeg("Alles is al gelijk.")
        return
    if naar_lokaal:
        zeg("Van de site naar Huis inspo:")
        regels(naar_lokaal, "← lokaal")
    if op_site:
        zeg("Naar de site:")
        regels(op_site, "→ site ")
        zeg("  samen %s versleuteld" % mb(sum(len(v) for v in op_site.values() if v)))


def bewaar_kopie(pad, steun):
    if os.path.exists(pad):
        doel = os.path.join(steun, os.path.relpath(pad, BRON))
        os.makedirs(os.path.dirname(doel), exist_ok=True)
        shutil.move(pad, doel)


def cmd_sync(a):
    sleutel = lees_sleutel()
    for poging in range(3):
        vers_clone()
        info_pad = os.path.join(CLONE, "huis", "sleutel.json")
        if not os.path.isfile(info_pad):
            stop("Het huis heeft nog geen wachtwoord. Draai eerst: huis-tools/sync.sh init")
        if not kluis.controleer(base64.b64decode(sleutel), json.load(open(info_pad))):
            stop("De sleutel op deze Mac past niet meer (ander wachtwoord?). Draai: huis-tools/sync.sh init")
        k = kluis.Kluis(sleutel, os.path.join(CLONE, "huis", "kluis"))
        if poging == 0:
            waarschuw_werkmap()
        op_site, naar_lokaal, conflicten, idx = plan(k, a.neem)
        if conflicten:
            zeg("Aan beide kanten veranderd:")
            for c in conflicten:
                zeg("  " + c)
            stop("Niets gedaan. Kies welke kant wint met --neem site of --neem lokaal.", 2)
        toon_plan(op_site, naar_lokaal, idx)
        if not op_site and not naar_lokaal:
            return
        if a.droog:
            return
        if poging == 0 and not a.ja:
            if not sys.stdin.isatty():
                stop("Zeker weten? Draai opnieuw met --ja (of in een terminal, dan vraagt hij het).")
            if input("Doorgaan? [j/N] ").strip().lower() not in ("j", "ja", "y", "yes"):
                stop("Gestopt.")
        steun = os.path.join(STEUN, "vervangen", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        for kp, v in naar_lokaal.items():
            p = lokaal_pad(kp)
            data = k.lees(kp) if isinstance(v, tuple) else v
            bewaar_kopie(p, steun)
            if data is not None:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
        k.werk_bij(op_site, oud=idx)
        # nieuwe basis: zo staat het nu aan beide kanten
        idx = k.index()
        tweeweg_pref = tuple(p for p, _ in TWEEWEG)
        k.schrijf_json(kluis.BASIS, {
            "bestanden": {p: v["sha"] for p, v in idx.items() if p.startswith(tweeweg_pref)},
            "json": {p: k.lees_json(p) for p in JSON_LIJSTEN if p in idx}})
        bekend = {k.naam(p) for p in idx} | {k.naam(kluis.INDEX), k.naam(kluis.BASIS)}
        for n in os.listdir(k.map):                    # wezen van eerdere versies
            if n not in bekend:
                os.remove(os.path.join(k.map, n))
        uitslag = push("Huis bijgewerkt vanaf de Mac")
        if uitslag in ("ok", "niets"):
            if os.path.isdir(steun):
                zeg("Vervangen lokale bestanden staan in %s" % steun)
            zeg("Klaar. Over een minuut of twee staat het op https://vangenderingen.nl/huis/")
            return
        zeg("De site veranderde tussendoor (importer?); opnieuw…")
    stop("Lukte drie keer niet. Probeer het later opnieuw.")


def main():
    ap = argparse.ArgumentParser(description="Huis inspo <-> vangenderingen.nl/huis")
    ap.add_argument("opdracht", nargs="?", default="sync", choices=("sync", "init", "wachtwoord"))
    ap.add_argument("--droog", action="store_true", help="alleen laten zien wat er zou gebeuren")
    ap.add_argument("--ja", action="store_true", help="niet vragen")
    ap.add_argument("--neem", choices=("site", "lokaal"), help="wie wint bij een conflict")
    a = ap.parse_args()
    if not os.path.isdir(BRON):
        stop("Huis inspo niet gevonden op %s (zet HUIS_INSPO=...)." % BRON)
    {"sync": cmd_sync, "init": cmd_init, "wachtwoord": cmd_wachtwoord}[a.opdracht](a)


if __name__ == "__main__":
    main()
