# Het huis op vangenderingen.nl/huis/

Het 3D-model van de Vergierdeweg en de meubelimporter, op de eigen site. De bron blijft **Huis inspo** op de Mac;
deze repo bevat alleen een versleutelde kopie en de code om die te openen.

| Wat | Waar |
|---|---|
| Startpagina (wachtwoord, menu) | https://vangenderingen.nl/huis/ |
| Het huis (webmodel) | https://vangenderingen.nl/huis/model/ |
| Meubels importeren | https://vangenderingen.nl/huis/importeer/ |

## Eerste keer instellen

1. **Wachtwoord kiezen** (in een terminal, of in Claude Code met `!` ervoor):
   ```bash
   huis-tools/sync.sh init
   ```
   Het script vraagt het wachtwoord twee keer, zet `huis/sleutel.json` op GitHub en bewaart de sleutel op deze Mac
   in `~/.config/huis/site-sleutel`. Het toont daarna de **sleutel voor GitHub**.
2. **Geheim in GitHub:** repo → *Settings → Secrets and variables → Actions → New repository secret*,
   naam `HUIS_SLEUTEL`, waarde = die sleutel. Zonder dit geheim werkt de importer op de site niet (het huis wel).
3. **Het huis erop zetten:**
   ```bash
   huis-tools/sync.sh
   ```
4. Open https://vangenderingen.nl/huis/ (na een paar minuten), vul het wachtwoord in. Bij **Meubels importeren**
   vraagt de pagina eenmalig per browser om een GitHub-token; de stappen staan erbij (fine-grained token, alleen deze
   repo, *Actions* en *Contents* op *Read and write*).

Op een andere Mac: `huis-tools/sync.sh init` vraagt dan het bestaande wachtwoord en bewaart alleen de sleutel.

## Synchroniseren

```bash
huis-tools/sync.sh            # laat zien wat er gebeurt en vraagt of het mag
huis-tools/sync.sh --droog    # alleen laten zien
huis-tools/sync.sh --ja       # zonder vragen
```

- **Alleen naar de site:** het webmodel, de importpagina en de importer-code zoals ze op **main** van Huis inspo
  staan (niet-gecommitte wijzigingen gaan niet mee; het script waarschuwt), plus de foto's en inspo.
- **Twee kanten op:** `HuisGame/Meubels/` en `HuisGame/Tools/web-export/meubels/`. Wat je op de site importeert komt
  zo in Huis inspo (ook voor de Unreal-game), en wat je lokaal importeert gaat de site op.
- **Conflict** (hetzelfde bestand aan beide kanten anders): het script doet niets en noemt het bestand. Kies dan
  `--neem site` of `--neem lokaal`. De catalogi worden per meubel samengevoegd; alleen een meubel dat aan beide
  kanten veranderde is een conflict.
- Wat het lokaal vervangt of weghaalt, staat daarna in `~/Library/Application Support/huis-site/vervangen/<tijd>/`.
- Het werkt in een eigen clone (`~/Library/Caches/huis-site/repo`), nooit in je checkout van deze repo.
- Importeer niet tegelijk lokaal (server.py) terwijl het script loopt.

**Nieuw wachtwoord:** `huis-tools/sync.sh wachtwoord` versleutelt alles opnieuw. Zet daarna de nieuwe sleutel als
`HUIS_SLEUTEL` in GitHub. Browsers vragen vanzelf om het nieuwe wachtwoord.

## Hoe het werkt

- **Versleuteling** (`kluis.py`, `../huis/slot.js`): PBKDF2-SHA256 (600.000 rondes) → AES-256-GCM per bestand,
  met het pad als associated data. Ook de bestandsnamen zijn versleuteld (HMAC), dus in de openbare repo is niet te
  zien welke meubels of foto's er zijn. `huis/kluis/<naam van "_index">` somt alles op.
- **Lezen in de browser:** `../huis/sw.js` (service worker) ontsleutelt alles onder `/huis/model/` en
  `/huis/importeer/`. Het webmodel zelf is ongewijzigd. Ontwerpen bewaart het webmodel hier alleen in de browser
  (Exporteren/Importeren om ze mee te nemen).
- **Importer:** de importpagina van Huis inspo met `../huis/github.js` ervoor (sync zet die erin). Draaien, kantelen,
  eenheid en schalen gebeuren meteen in de browser (zelfde rekenwerk als `normaliseer` in importeer.py). Voor de
  zware stappen start hij de workflow `.github/workflows/huis-importer.yml`: die draait `runner.py` met de echte
  `server.py`/`importeer.py` (uit de kluis), zet wijzigingen versleuteld op main en het antwoord op de tak
  `huis-werk`. Elke stap duurt ongeveer een minuut (de eerste keer langer: dan installeert hij de omgeving);
  rechtsboven zie je wat er loopt. Logs zijn openbaar maar bevatten niets uit de kluis.
- **Meshy (AI-modellen)** werkt alleen als er een geheim `MESHY_API_KEY` is én `"meshy": true` in `importer.json`
  (staat nu uit).

## Lokaal testen

`test/nep_github.py` doet GitHub Pages en de benodigde GitHub-API na, en draait `runner.py` bij elke "dispatch",
zodat de hele keten zonder de echte repo te testen is. Zet een bare repo met deze code op, synchroniseer erheen
met `HUIS_SITE_REPO=file://…`, `HUIS_SITE_CLONE`, `HUIS_SITE_SLEUTEL`, `HUIS_SITE_MAP` en `HUIS_INSPO` (een
worktree van Huis inspo, zodat de echte data niet geraakt wordt), en start:

```bash
HUIS_SLEUTEL="$(cat <testsleutel>)" HUIS_NODE_MODULES=".../HuisGame/Tools/meubels/node_modules" \
  python3.13 -I huis-tools/test/nep_github.py <bare-repo> 8790 <venv-met-importer-deps>/bin/python
```

Op `localhost` praten de pagina's vanzelf met `/_api` van die server; elk token behalve `fout` is goed.
