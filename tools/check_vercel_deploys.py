#!/usr/bin/env python3
"""Fraîcheur de déploiement des FRONTS Vercel, en une commande.

Pendant Railway (back) on a `check_all_deploys.py` ; ici on couvre les fronts
Vercel (HUB, BUD, VIU, ANA, TAK, EST, CON, MAT, ADM, landing). Cause de
plusieurs fausses régressions : tester un front PENDANT son build Vercel (ancien
bundle encore servi) ou un build ERROR/CANCELED.

Pour chaque projet Vercel, compare le commit du dernier déploiement PRODUCTION
**READY** (= ce qui est réellement servi) au git HEAD du monorepo, et signale
l'état du déploiement le plus récent (READY / BUILDING / ERROR / CANCELED).

Auth : réutilise le token du Vercel CLI (auth.json) ou $VERCEL_TOKEN.

Usage (depuis le monorepo) :
    python tools/check_vercel_deploys.py

Sortie : tableau par front ; exit 1 si AU MOINS un front a un build récent
non-READY (BUILDING/ERROR/CANCELED) au-dessus du live -> ne pas s'y fier.

Note monorepo : un front dont le HEAD n'a pas touché les fichiers peut rester
légitimement « behind » (Vercel saute le rebuild). « behind » seul n'est pas une
erreur ; c'est l'état BUILDING/ERROR du dernier déploiement qui alerte.
"""
import json
import os
import subprocess
import sys
import urllib.request

TEAM_SLUG = "simon-hacheys-projects"
# Fronts à surveiller (nom de projet Vercel). None -> auto-découverte.
FRONTS = [
    "adision-app-client", "ad-budget-client", "adision-viu-client",
    "ad-ana-client", "ad-tak-client", "ad-est-client", "ad-con-client",
    "adision-monorepo-ad-mat", "ad-adm-client", "adision-landing",
]


def _token():
    if os.environ.get("VERCEL_TOKEN"):
        return os.environ["VERCEL_TOKEN"]
    appdata = os.environ.get("APPDATA", "")
    home = os.path.expanduser("~")
    for p in (
        os.path.join(appdata, "xdg.data", "com.vercel.cli", "auth.json"),
        os.path.join(appdata, "com.vercel.cli", "auth.json"),
        os.path.join(home, ".local", "share", "com.vercel.cli", "auth.json"),
        os.path.join(home, ".config", "com.vercel.cli", "auth.json"),
    ):
        if os.path.isfile(p):
            try:
                d = json.load(open(p))
                if d.get("token"):
                    return d["token"]
            except Exception:
                pass
    return None


def _git_head():
    # Les fronts Vercel déploient depuis le MONOREPO -> on lit SON HEAD,
    # pas celui d'adision-api (où vit ce script, à côté de check_all_deploys).
    api_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monorepo = os.path.join(os.path.dirname(api_dir), "adision-monorepo")
    cwd = monorepo if os.path.isdir(os.path.join(monorepo, ".git")) else api_dir
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                          capture_output=True, text=True, shell=True).stdout.strip()


def _api(path, token):
    req = urllib.request.Request("https://api.vercel.com" + path,
                                 headers={"Authorization": "Bearer " + token})
    return json.load(urllib.request.urlopen(req, timeout=30))


def main():
    token = _token()
    if not token:
        print("[?] Token Vercel introuvable (lance `vercel login` ou pose $VERCEL_TOKEN).")
        return 2
    head = _git_head()
    try:
        teams = _api("/v2/teams", token).get("teams", [])
    except Exception as e:
        print(f"[?] API Vercel inaccessible : {e}")
        return 2
    tid = next((t["id"] for t in teams if t.get("slug") == TEAM_SLUG),
               teams[0]["id"] if teams else None)

    print(f"monorepo HEAD : {head[:8]}")
    print(f"{'front':26} {'live (READY)':13} {'dernier':13} verdict")
    print("-" * 70)
    problems = 0
    for name in FRONTS:
        try:
            deps = _api(f"/v6/deployments?app={name}&target=production&limit=10&teamId={tid}",
                        token).get("deployments", [])
        except Exception as e:
            print(f"{name:26} {'?':13} {'?':13} (API: {e})")
            continue
        if not deps:
            print(f"{name:26} {'aucun':13} {'-':13} (pas de prod)")
            continue
        newest = deps[0]
        newest_state = newest.get("state") or newest.get("readyState") or "?"
        newest_sha = (newest.get("meta") or {}).get("githubCommitSha", "") or ""
        live = next((d for d in deps if (d.get("state") or d.get("readyState")) == "READY"), None)
        live_sha = ((live or {}).get("meta") or {}).get("githubCommitSha", "") or ""

        live_ok = live_sha[:8] == head[:8]
        # BUILDING = en cours (ne pas tester). ERROR = build cassé (live périmé).
        # CANCELED = build SAUTÉ par l'ignored-build-step (aucun fichier du front
        # changé) -> bénin : le front sert le dernier build pertinent.
        building = newest_state in ("BUILDING", "INITIALIZING", "QUEUED", "DEPLOYING")
        errored = newest_state == "ERROR"
        if building:
            verdict = f"[BUILD] {newest_sha[:8]} en cours — NE PAS tester"
            problems += 1
        elif errored and newest_sha[:8] == head[:8]:
            verdict = f"[X] build {newest_sha[:8]} ERROR — live perime"
            problems += 1
        elif live_ok:
            verdict = "[OK] live = HEAD"
        elif newest_state == "CANCELED":
            verdict = f"[skip] live={live_sha[:8]} (HEAD non pertinent pour ce front)"
        else:
            verdict = f"[behind] live={live_sha[:8]} != HEAD"
        print(f"{name:26} {live_sha[:8]:13} {(newest_sha[:8]+' '+newest_state):20} {verdict}")
    print("-" * 70)
    if problems:
        print(f"{problems} front(s) en build/cassé -> ne pas tester ceux-là maintenant.")
        return 1
    print("Fronts live cohérents (les 'behind' = non réaffectés par HEAD).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
