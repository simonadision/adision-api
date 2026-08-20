"""Invariant B — LE PLUS IMPORTANT : le client (écran) et le serveur calculent
LE MÊME total de ligne budget.

Chantier « tests d'invariant sur les chiffres que Simon voit » (20 août 2026) :
c'est l'invariant qui aurait attrapé l'écart de 39 219 $ du 20 août 2026
(projet 290) — deux implémentations indépendantes du même calcul, jamais
comparées automatiquement avant ce fichier.

Fixture PARTAGÉE — UN SEUL fichier JSON, lu TEL QUEL des deux côtés (aucune
copie, cf. son champ "note") :
    adision-monorepo/apps/ad-bud/src/utils/__fixtures__/invariantLignesBudget.json

Python : modules.budget_fingerprint._line_total + modules.aggregates.
heures_effectives — la fonction canonique serveur posée par a07c72b
(2026-08-20, « verrou serveur — production_valeur prioritaire sur la colonne
heures »).
JS     : apps/ad-bud/src/utils/budgetLigneTotal.js::computeLigneTotal — LA
MÊME fonction que App.jsx::getRow appelle réellement depuis ce chantier
(App.jsx importe budgetLigneTotal.js ; ce n'est pas un mirroir séparé qui
pourrait diverger en silence — cf. la découverte documentée dans
test_budget_fingerprint_production_valeur_discovery.py, exactement ce défaut,
mais sur le mirroir de l'EMPREINTE, distinct de celui de l'ÉCRAN testé ici).

Portée — comme son patron test_budget_fingerprint_agreement.py : ce test a
besoin du dépôt FRÈRE adision-monorepo ET de node. Il ne tourne donc PAS dans
une CI GitHub (checkout d'un second dépôt privé = jeton multi-dépôt, décision
déjà actée dans adision-monorepo/.github/workflows/ci.yml — « NE TOURNE PAS
ICI, ET CE N'EST PAS UN OUBLI »), seulement au pre-push local (hooks/pre-push)
où les deux dépôts sont déjà frères sur le poste de Simon. Si node ou le dépôt
frère sont absents, le test SKIP explicitement (jamais un faux vert silencieux).

Lancer :  python tests/test_invariant_b_ligne_python_js.py
     ou :  pytest tests/test_invariant_b_ligne_python_js.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import pytest  # noqa: E402

from modules.aggregates import heures_effectives  # noqa: E402
from modules.budget_fingerprint import _line_total  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.dirname(os.path.dirname(HERE))
FIXTURE = os.path.join(RD, "adision-monorepo", "apps", "ad-bud", "src", "utils",
                        "__fixtures__", "invariantLignesBudget.json")
JS_CLI = os.path.join(RD, "adision-monorepo", "apps", "ad-bud", "src", "utils",
                       "budgetLigneTotal.node.mjs")

_DISPONIBLE = os.path.isfile(FIXTURE) and os.path.isfile(JS_CLI)
_RAISON_SKIP = "dépôt frère adision-monorepo ou node absent — invariant B cross-langage réservé au pre-push local"


def _charger_fixture():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _resultats_python(data):
    out = {}
    for cas in data["cas"]:
        arr = bool(cas.get("arrondi_dollar"))
        ligne = cas["ligne"]
        qte = float(ligne.get("qte") or 0)
        heures = heures_effectives(ligne.get("unite"), ligne.get("heures"),
                                    ligne.get("heures_manuelles"), qte,
                                    ligne.get("production_valeur"))
        total = _line_total(arr, ligne)
        out[cas["id"]] = {"heures": heures, "total": total}
    return out


def _resultats_js():
    r = subprocess.run(["node", JS_CLI, FIXTURE], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"node CLI (budgetLigneTotal.node.mjs) a échoué : {r.stderr.strip()[:500]}")
    return json.loads(r.stdout)


def _comparer(data, py, js):
    ecarts = []
    for cas in data["cas"]:
        cid = cas["id"]
        p, j = py[cid], js[cid]
        if abs(p["heures"] - j["heures"]) > 1e-9:
            ecarts.append(f"{cid} ({cas.get('note', '')[:60]}) : heures Python={p['heures']} JS={j['heures']}")
        if abs(p["total"] - j["total"]) > 1e-6:
            ecarts.append(f"{cid} ({cas.get('note', '')[:60]}) : total Python={p['total']} JS={j['total']}")
    return ecarts


@pytest.mark.skipif(not _DISPONIBLE, reason=_RAISON_SKIP)
def test_invariant_b_ligne_python_js():
    data = _charger_fixture()
    py = _resultats_python(data)
    js = _resultats_js()
    ecarts = _comparer(data, py, js)
    assert not ecarts, "Invariant B rompu (Python != JS pour au moins un cas) :\n" + "\n".join(ecarts)


# ── Preuve isolée du cas RÉEL de l'incident (critère d'acceptation #2) ──────
# Ce test-ci n'a besoin NI de node NI du dépôt frère : il prouve que Python
# SEUL, sur les données réelles de l'incident (isolant à 1 800 pi², 96 pi²/h,
# heures stockée = 149,40), donne le résultat attendu (18,8 h / 5 440 $) —
# la moitié de la preuve qui tourne partout, y compris en CI. L'autre moitié
# (Python == JS, ci-dessus) reste réservée au pre-push par nécessité d'accès
# au dépôt frère.
def test_isolant_incident_290_valeur_attendue():
    ligne = {
        "id": 1, "unite": "pi2", "qte": 1800, "prix_unitaire": 2.5,
        "heures": 149.40, "heures_manuelles": False, "taux_horaire": 50,
        "production_valeur": 96,
    }
    heures = heures_effectives("pi2", 149.40, False, 1800, 96)
    total = _line_total(False, ligne)
    assert heures == pytest.approx(18.8)
    assert total == pytest.approx(5440.0)


if __name__ == "__main__":
    if not _DISPONIBLE:
        print(f"[SKIP] {_RAISON_SKIP}")
        sys.exit(0)
    data = _charger_fixture()
    py = _resultats_python(data)
    js = _resultats_js()
    ecarts = _comparer(data, py, js)
    for cas in data["cas"]:
        cid = cas["id"]
        touche = any(cid in e for e in ecarts)
        if touche:
            print(f"  [X] {cid}")
        else:
            print(f"  [OK] {cid:35} heures={py[cid]['heures']} total={py[cid]['total']}")
    print(f"\n{len(data['cas']) - len({e.split(' ')[0] for e in ecarts})}/{len(data['cas'])} cas : Python == JS")
    sys.exit(1 if ecarts else 0)
