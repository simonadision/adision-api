#!/usr/bin/env python3
"""Fraîcheur de déploiement de TOUS les services Railway en une commande.

Pour chaque repo frère (*-api lié à un projet Railway), compare git HEAD au
commit RÉELLEMENT déployé (railway status --json -> activeDeployment.meta.
commitHash) et son statut. But : ne JAMAIS tester pendant un build (« pushé !=
déployé »), cause d'une fausse régression vue cette session (takeoff TAK créé
pendant le build -> ancien code).

Usage (depuis n'importe quel repo, ou le dossier parent) :
    python adision-api/scripts/check_all_deploys.py

Sortie : tableau par service ; exit 1 si AU MOINS un service a déployé != HEAD
ou status != SUCCESS (deploy en cours / non déclenché)."""
import json
import os
import subprocess
import sys

# Repos frères susceptibles d'être liés à un service Railway. On tente chacun ;
# ceux non liés / sans railway sont signalés « n/a » sans faire échouer.
REPOS = [
    "adision-api", "adision-app-api", "adision-viu-api", "adision-tak-api",
    "adision-est-api", "adision-con-api", "adision-mat-api",
]


def _run(args, cwd):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                              shell=True, timeout=60).stdout
    except Exception:
        return ""


def _head(cwd):
    return _run(["git", "rev-parse", "HEAD"], cwd).strip()


def _deployed(cwd):
    raw = _run(["railway", "status", "--json"], cwd)
    if not raw.strip():
        return None
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return None
    best = None
    for env in d.get("environments", {}).get("edges", []):
        for si in env["node"].get("serviceInstances", {}).get("edges", []):
            for dep in si["node"].get("activeDeployments", []):
                ch = (dep.get("meta") or {}).get("commitHash")
                ca = dep.get("createdAt") or ""
                if ch and (best is None or ca > best[1]):
                    best = (ch, ca, dep.get("status"))
    return best


def main():
    parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print(f"{'service':18} {'HEAD':10} {'déployé':10} {'status':12} verdict")
    print("-" * 64)
    stale = 0
    checked = 0
    for name in REPOS:
        cwd = os.path.join(parent, name)
        if not os.path.isdir(os.path.join(cwd, ".git")):
            continue
        head = _head(cwd)
        if not head:
            continue
        dep = _deployed(cwd)
        if dep is None:
            print(f"{name:18} {head[:8]:10} {'n/a':10} {'n/a':12} (non lié Railway)")
            continue
        checked += 1
        commit, _ca, status = dep
        ok = (head[:12].startswith(commit[:12]) or commit[:12].startswith(head[:12])) and status == "SUCCESS"
        verdict = "[OK]" if ok else "[X] pas à jour"
        if not ok:
            stale += 1
        print(f"{name:18} {head[:8]:10} {commit[:8]:10} {str(status):12} {verdict}")
    print("-" * 64)
    if stale:
        print(f"{stale}/{checked} service(s) PAS à jour (build en cours ou non déclenché) "
              f"-> NE PAS tester ces services maintenant.")
        return 1
    print(f"{checked} service(s) à jour — sûr pour tester.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
