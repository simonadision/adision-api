"""Unité « % » (1er oct. 2026, Simon : « l'unité % est censée affecter la
quantité en pourcentage »). Miroir de packages/aggregates/__tests__/
quantiteEffective.test.js (monorepo) : mêmes cas."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.aggregates import quantite_effective  # noqa: E402
from modules.budget_fingerprint import _line_total  # noqa: E402


def test_unite_pourcent_est_un_pourcentage():
    assert quantite_effective(2, "%") == 0.02
    assert quantite_effective("4", " % ") == 0.04


def test_autres_unites_inchangees():
    assert quantite_effective(2, "global") == 2
    assert quantite_effective(2, None) == 2


def test_cas_de_simon_contingence():
    # Ligne Contingence, qté 2, unité %, coût unitaire 1 010 098 $.
    t = _line_total(False, {"qte": 2, "unite": "%", "prix_unitaire": 1010098})
    assert abs(t - 20201.96) < 1e-6, t


def test_contremaitre_en_pourcent_les_heures_ne_bougent_pas():
    # Budget 318 (copie) : trois « Contremaître » en unité %, 320 h chacune
    # au plus — leurs heures ne doivent JAMAIS être divisées par 100.
    t = _line_total(False, {"qte": 320, "unite": "%", "heures": 320, "taux_horaire": 80})
    assert t == 320 * 80, t


def test_facteur_d_unite_multiplie_la_quantite():
    # Clôture de chantier : 1139 plin/mois, 6 mois.
    assert quantite_effective(1139, "plin/mois", 6) == 6834
    assert quantite_effective("1139", "plin/mois", "6") == 6834


def test_facteur_absent_nul_ou_negatif_vaut_un():
    for f in (None, "", 0, -3, "abc"):
        assert quantite_effective(1139, "plin/mois", f) == 1139


def test_facteur_et_pourcentage_se_composent():
    assert quantite_effective(2, "%", 3) == 0.06


# ── Le facteur BOUGE l'empreinte du budget (2 oct. 2026, condition de PC3) ──
# Contrairement aux champs neutres, qte_facteur ENTRE dans le montant. Si un
# jour il cessait d'entrer dans _line_total, un devis émis ne se régénérerait
# plus après un changement de facteur. Ces cas ÉPROUVENT la chose (facteur
# 1 -> 6, l'empreinte DOIT changer). Rouge sans le correctif, mesuré.
# Ici plutôt que dans un fichier à part : ce fichier est DÉJÀ câblé en CI.
import copy  # noqa: E402

from modules import budget_fingerprint as FP  # noqa: E402

PROJET = {"arrondi_dollar": False, "pct_admin": 15, "pct_profit": 0}
CLOTURE = {"id": 1, "qte": 1139, "unite": "plin/mois", "prix_unitaire": 5, "ajust_materiaux": 0}


def test_le_facteur_multiplie_le_total_de_ligne():
    sans = FP._line_total(False, dict(CLOTURE))
    avec = FP._line_total(False, {**CLOTURE, "qte_facteur": 6})
    assert round(sans, 2) == 5695.00
    assert round(avec, 2) == 34170.00  # 1139 × 6 × 5 $


def test_changer_le_facteur_change_l_empreinte():
    base = [dict(CLOTURE)]
    six = [{**CLOTURE, "qte_facteur": 6}]
    assert FP.compute_budget_fingerprint(PROJET, base, 0) != FP.compute_budget_fingerprint(PROJET, six, 0)


def test_facteur_null_ou_un_ne_change_rien():
    base = FP.compute_budget_fingerprint(PROJET, [dict(CLOTURE)], 0)
    for f in (None, 1, "1"):
        lignes = [copy.deepcopy({**CLOTURE, "qte_facteur": f})]
        assert FP.compute_budget_fingerprint(PROJET, lignes, 0) == base


# ── pouce × plin (2 oct. 2026) : qté en POUCES, facteur en PLIN -> pi² ──────
def test_pouce_plin_convertit_les_pouces_en_pieds():
    # L'exemple de Simon, résultat confirmé par lui : 6 po × 10 plin = 5 pi².
    for u in ("pouceXplin", "pouce*plin", "POUCE x PLIN", "pouce×plin", " pouces * plin "):
        assert quantite_effective(6, u, 10) == 5, u


def test_pouce_plin_sans_facteur_reste_la_quantite():
    assert quantite_effective(6, "pouceXplin", None) == 6


def test_autres_unites_non_touchees_par_la_conversion():
    assert quantite_effective(6, "plin/mois", 10) == 60
    assert quantite_effective(6, "plin", 10) == 60
