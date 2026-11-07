"""Nep-GitHub om de site en de importer lokaal te testen, zonder de echte repo te raken.

  python3 -I huis-tools/test/nep_github.py <bare-repo> <poort> <python-met-importer-deps>

- Serveert de site zoals GitHub Pages: elk pad komt uit <bare-repo> op main (dus altijd de laatste push).
- /_api/... doet de stukjes GitHub-API na die huis/slot.js, huis/sw.js en huis/github.js gebruiken
  (refs, contents, blobs/trees/commits, workflow dispatch en runs). Token: alles behalve "fout" is goed.
- Een dispatch draait huis-tools/runner.py zoals de Action dat doet: verse clone, voorbereiden, uitvoeren
  (HUIS_SLEUTEL uit de omgeving van deze server). node_modules komt uit $HUIS_NODE_MODULES.
De pagina's gebruiken deze API vanzelf op localhost (zie apiBasis in huis/slot.js).
"""
import base64
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid

BARE, POORT, PY = os.path.abspath(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
REPO = "vanGenderingen/vanGenderingen.github.io"
SOORTEN = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json",
           ".css": "text/css", ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml"}
RUNS = []                                       # [{display_title, status, conclusion, html_url}]
SLOT = threading.Lock()


def git(*args, invoer=None, env=None, check=True):
    r = subprocess.run(["git", *args], cwd=BARE, capture_output=True, input=invoer, env=dict(os.environ, **(env or {})))
    if check and r.returncode:
        raise RuntimeError(r.stderr.decode())
    return r


def ref_sha(tak):
    r = git("rev-parse", "-q", "--verify", "refs/heads/" + tak, check=False)
    return r.stdout.decode().strip() or None


def job(id_, verzoek, run):
    werk = tempfile.mkdtemp(prefix="nep-actie-")
    try:
        ws = os.path.join(werk, "repo")
        subprocess.run(["git", "clone", "-q", "--depth=1", "file://" + BARE, ws], check=True)
        for k, v in (("user.name", "github-actions[bot]"), ("user.email", "bot@invalid")):
            subprocess.run(["git", "config", k, v], cwd=ws, check=True)
        env = dict(os.environ, GITHUB_WORKSPACE=ws, HUIS_WERK=os.path.join(werk, "huis"), RUNNER_TEMP=werk,
                   HUIS_ID=id_, HUIS_VERZOEK=verzoek, GITHUB_OUTPUT=os.path.join(werk, "uit"))
        env.pop("GH_TOKEN", None)
        run["status"] = "in_progress"
        r = subprocess.run([PY, "-I", "huis-tools/runner.py", "voorbereiden"], cwd=ws, env=env, capture_output=True, text=True)
        if r.returncode == 0:
            nm = os.environ.get("HUIS_NODE_MODULES")
            if nm:
                os.symlink(nm, os.path.join(werk, "huis", "HuisGame", "Tools", "meubels", "node_modules"))
            r = subprocess.run([PY, "-I", "huis-tools/runner.py", "uitvoeren"], cwd=ws, env=env, capture_output=True, text=True)
        run["log"] = (r.stdout + r.stderr)[-4000:]
        sys.stderr.write("[actie %s] %s\n" % (id_, run["log"].strip()[-600:]))
        run["conclusion"] = "success" if r.returncode == 0 else "failure"
    except Exception as e:
        run["conclusion"], run["log"] = "failure", str(e)
    finally:
        run["status"] = "completed"
        shutil.rmtree(werk, ignore_errors=True)


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        if "/runs" not in self.path and "huis-werk" not in self.path:
            sys.stderr.write("%s\n" % (fmt % a))

    def stuur(self, code, body=b"", soort="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", soort)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def api(self, methode):
        if (self.headers.get("Authorization") or "") in ("", "Bearer fout"):
            return self.stuur(401, {"message": "Bad credentials"})
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        pref = "/_api/repos/%s/" % REPO
        if not u.path.startswith(pref):
            return self.stuur(404, {"message": "Not Found"})
        p = urllib.parse.unquote(u.path[len(pref):])
        with SLOT:
            if methode == "GET" and p.startswith("git/ref/heads/"):
                sha = ref_sha(p[len("git/ref/heads/"):])
                return self.stuur(200, {"object": {"sha": sha}}) if sha else self.stuur(404, {"message": "Not Found"})
            if methode == "GET" and p.startswith("contents/"):
                ref = (q.get("ref") or ["main"])[0]
                r = git("show", "%s:%s" % (ref, p[len("contents/"):]), check=False)
                return self.stuur(200, r.stdout, "application/octet-stream") if r.returncode == 0 else self.stuur(404, {"message": "Not Found"})
            if methode == "POST" and p == "git/blobs":
                b = self.body()
                sha = git("hash-object", "-w", "--stdin", invoer=base64.b64decode(b["content"])).stdout.decode().strip()
                return self.stuur(201, {"sha": sha})
            if methode == "GET" and p.startswith("git/commits/"):
                boom = git("rev-parse", p[len("git/commits/"):] + "^{tree}").stdout.decode().strip()
                return self.stuur(200, {"tree": {"sha": boom}})
            if methode == "POST" and p == "git/trees":
                b = self.body()
                idx = os.path.join(tempfile.gettempdir(), "nep-index-" + uuid.uuid4().hex)
                env = {"GIT_INDEX_FILE": idx}
                if b.get("base_tree"):
                    git("read-tree", b["base_tree"], env=env)
                for t in b["tree"]:
                    git("update-index", "--add", "--cacheinfo", "%s,%s,%s" % (t["mode"], t["sha"], t["path"]), env=env)
                boom = git("write-tree", env=env).stdout.decode().strip()
                os.remove(idx)
                return self.stuur(201, {"sha": boom})
            if methode == "POST" and p == "git/commits":
                b = self.body()
                args = ["commit-tree", b["tree"], "-m", b.get("message") or "x"]
                for o in b.get("parents") or []:
                    args += ["-p", o]
                env = {"GIT_AUTHOR_NAME": "web", "GIT_AUTHOR_EMAIL": "w@invalid", "GIT_COMMITTER_NAME": "web", "GIT_COMMITTER_EMAIL": "w@invalid"}
                return self.stuur(201, {"sha": git(*args, invoer=b"", env=env).stdout.decode().strip()})
            if methode == "PATCH" and p.startswith("git/refs/heads/"):
                b, tak = self.body(), p[len("git/refs/heads/"):]
                oud = ref_sha(tak)
                if oud and not b.get("force") and git("merge-base", "--is-ancestor", oud, b["sha"], check=False).returncode:
                    return self.stuur(422, {"message": "Update is not a fast forward"})
                git("update-ref", "refs/heads/" + tak, b["sha"])
                return self.stuur(200, {"object": {"sha": b["sha"]}})
            if methode == "POST" and p == "git/refs":
                b = self.body()
                if ref_sha(b["ref"][len("refs/heads/"):]):
                    return self.stuur(422, {"message": "Reference already exists"})
                git("update-ref", b["ref"], b["sha"])
                return self.stuur(201, {"ref": b["ref"]})
        if methode == "GET" and p == "actions/workflows/huis-importer.yml":
            return self.stuur(200, {"name": "Huis importer"})
        if methode == "GET" and p.startswith("actions/workflows/huis-importer.yml/runs"):
            return self.stuur(200, {"workflow_runs": [dict(r, log=None) for r in reversed(RUNS)][:15]})
        if methode == "POST" and p == "actions/workflows/huis-importer.yml/dispatches":
            b = self.body()
            run = {"display_title": "huis " + b["inputs"]["id"], "status": "queued", "conclusion": None,
                   "html_url": "http://localhost:%d/_runs/%d" % (POORT, len(RUNS))}
            RUNS.append(run)
            threading.Thread(target=job, args=(b["inputs"]["id"], b["inputs"]["verzoek"], run), daemon=True).start()
            return self.stuur(204)
        return self.stuur(404, {"message": "Not Found (nep)"})

    def do_GET(self):
        if self.path.startswith("/_api/"):
            return self.api("GET")
        p = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if p.startswith("/_runs/"):
            return self.stuur(200, RUNS[int(p[7:])].get("log") or "", "text/plain; charset=utf-8")
        rel = p.lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        r = git("show", "main:" + rel, check=False)
        if r.returncode and git("cat-file", "-e", "main:" + rel + "/index.html", check=False).returncode == 0:
            self.send_response(301); self.send_header("Location", p + "/"); self.send_header("Content-Length", "0")
            return self.end_headers()
        if r.returncode:
            return self.stuur(404, b"404 (nep-Pages)", "text/plain")
        self.stuur(200, r.stdout, SOORTEN.get(os.path.splitext(rel)[1], "application/octet-stream"))

    def do_POST(self):
        return self.api("POST") if self.path.startswith("/_api/") else self.stuur(405)

    def do_PATCH(self):
        return self.api("PATCH")


if __name__ == "__main__":
    print("Nep-GitHub op http://localhost:%d (repo %s)" % (POORT, BARE), flush=True)
    http.server.ThreadingHTTPServer.request_queue_size = 128
    http.server.ThreadingHTTPServer(("127.0.0.1", POORT), H).serve_forever()
