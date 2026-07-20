"""ACCORD CROSS-LANGAGE de l'empreinte budget devis : Python == JavaScript.

L'empreinte est calculée par le CLIENT (JS, à la génération) et RECALCULÉE par le
SERVEUR (Python, à l'émission). Si les deux implémentations divergent d'un octet,
toute émission signalerait une fausse dérive. Ce test verrouille l'accord : pour
plusieurs fixtures (arrondi/non, ventilé/global, lignes horaires/ST/ajustements,
montants négatifs, pct absents), on compare la CHAÎNE CANONIQUE **et** le hash
produits par `budget_fingerprint.py` et par `apps/ad-bud/src/utils/
budgetFingerprint.js` (via node).

Lancer :  python tests/test_budget_fingerprint_agreement.py
     ou :  pytest tests/test_budget_fingerprint_agreement.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from modules import budget_fingerprint as FP  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.dirname(os.path.dirname(HERE))
JS_CLI = os.path.join(RD, "adision-monorepo", "apps", "ad-bud", "src", "utils", "budgetFingerprint.node.mjs")


def _line(**over):
    l = {
        "id": 1, "section": "03 31 00", "unite": "m2", "qte": 10, "prix_unitaire": 50,
        "ajust_materiaux": 0, "heures": 0, "heures_manuelles": False, "taux_horaire": 0,
        "sous_traitant_montant": 0, "ajust_main_oeuvre": 0, "ajust_sous_traitant": 0,
        "ajustement_pct": 0,
    }
    l.update(over)
    return l


def _projet(**over):
    p = {
        "revision_no": 1, "revision_label": "Originale", "arrondi_dollar": False,
        "pct_admin_mode": "global",
        "pct_admin_conditions": 10, "pct_admin_architecture": 15,
        "pct_admin_mecanique": None, "pct_admin_excavation": None,
    }
    p.update(over)
    return p


# Fixtures variées — chacune stresse un aspect du calcul.
FIXTURES = {
    "base": {
        "projet": _projet(),
        "lignes": [_line(), _line(id=2, section="01 00 00", qte=5, prix_unitaire=100)],
    },
    "arrondi": {
        "projet": _projet(arrondi_dollar=True, pct_admin_conditions=12.5),
        "lignes": [_line(qte=3, prix_unitaire=33.33, ajust_materiaux=7.5),
                   _line(id=2, section="20 00 00", heures=8, taux_horaire=45.5, unite="hre")],
    },
    "ventile_st_ajust": {
        "projet": _projet(pct_admin_mode="ventile", pct_admin_conditions_mat=5,
                          pct_admin_conditions_mo=8, pct_admin_conditions_st=3),
        "lignes": [
            _line(section="01 00 00", sous_traitant_montant=1200.75, ajust_sous_traitant=2.5),
            _line(id=2, section="02 10 00", qte=4, prix_unitaire=250, ajustement_pct=10),
            _line(id=3, section="31 00 00", heures=12, taux_horaire=60, unite="heures", ajust_main_oeuvre=-5),
        ],
    },
    "heures_manuelles_et_negatif": {
        "projet": _projet(arrondi_dollar=True),
        "lignes": [
            _line(unite="hre", heures=0, heures_manuelles=True, qte=99, taux_horaire=50),  # faux override -> qte
            _line(id=2, unite="hre", heures=6, heures_manuelles=True, taux_horaire=50),    # vrai override -> 6h
            _line(id=3, qte=-2, prix_unitaire=100),  # crédit (négatif)
        ],
    },
    "ligne_nulle_filtree": {
        "projet": _projet(),
        "lignes": [_line(), _line(id=2, qte=0, prix_unitaire=0), _line(id=3, section="20 00 00", qte=1, prix_unitaire=1)],
    },
}


def _server_montant(fx):
    # Montant « client » = montant serveur pour la fixture (les 2 côtés reçoivent
    # le MÊME montant : le test verrouille la CHAÎNE canonique, pas le calcul du
    # montant — déjà garanti par D2 / test_pdf_critical).
    from modules.ad_budget_api import _prix_vente_total
    # _prix_vente_total attend des lignes ACTIVES ; nos fixtures le sont toutes.
    return _prix_vente_total(fx["projet"], fx["lignes"])


def _js(fx_with_montant):
    p = tempfile.mktemp(suffix=".json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(fx_with_montant, fh, ensure_ascii=False)
    r = subprocess.run(["node", JS_CLI, p], capture_output=True, text=True, encoding="utf-8")
    try:
        os.remove(p)
    except OSError:
        pass
    if r.returncode != 0:
        raise RuntimeError(f"node CLI a échoué : {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def run_all():
    failed = 0
    for name, fx in FIXTURES.items():
        montant = _server_montant(fx)
        fx_m = {"projet": fx["projet"], "lignes": fx["lignes"], "montant": montant}
        py_canon = FP.canonical_string(fx["projet"], fx["lignes"], montant)
        py_hash = FP.compute_budget_fingerprint(fx["projet"], fx["lignes"], montant)
        js = _js(fx_m)
        ok_canon = py_canon == js["canonical"]
        ok_hash = py_hash == js["hash"]
        if ok_canon and ok_hash:
            print(f"  [OK] {name:28} hash={py_hash[:16]}… (montant={montant})")
        else:
            failed += 1
            print(f"  [X] {name} : canon={'=' if ok_canon else 'DIFF'} hash={'=' if ok_hash else 'DIFF'}")
            if not ok_canon:
                print("      --- PYTHON ---\n" + "\n".join("      " + l for l in py_canon.split("\n")))
                print("      --- JS ---\n" + "\n".join("      " + l for l in js["canonical"].split("\n")))
    print(f"\n{len(FIXTURES) - failed}/{len(FIXTURES)} fixtures : Python == JS")
    return failed


def test_fingerprint_agreement():
    assert run_all() == 0, "empreinte budget Python != JS (voir sortie)"


if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
