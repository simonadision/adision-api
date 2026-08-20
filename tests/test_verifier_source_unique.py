"""Filet de non-régression — Couche 3, source unique outillée (issue #9 monorepo).

Ne prouve pas seulement que tools/verifier_source_unique.py est vert aujourd'hui
(n'importe quel script vide l'est) : injecte une VRAIE copie suspecte dans un
VRAI fichier temporaire (tmp_path), lance le contrôle réel dessus (mêmes
fonctions que hooks/pre-push / CI) et vérifie qu'il la détecte pour de vrai.
Preuve que le contrôle mord, pas seulement qu'il ne dit jamais rien.
"""
import sys

import pytest

sys.path.insert(0, ".")
from tools.verifier_source_unique import (  # noqa: E402
    analyser_fichier,
    delegue,
    est_canonique,
    scanner,
    trouve_litteral_unites_heures,
    trouve_triade_champs_avec_arithmetique,
)

# Copie historique (forme exacte du bug côté JS pré-PR#12/#13, transposée en
# Python) : une liste _HEURE_UNITS locale + heures_manuelles, sans production_valeur.
COPIE_HISTORIQUE = '''
_HEURE_UNITS = {"hr", "hre", "heure", "heures", "h"}


def heures_effectives_copie_locale(unite, heures, heures_manuelles, qte):
    h = float(heures or 0)
    if str(unite or "").strip().lower() in _HEURE_UNITS and not (heures_manuelles and h != 0):
        return float(qte or 0)
    return h
'''

# Copie plus récente : référence bien production_valeur mais la recalcule en
# local au lieu de déléguer.
COPIE_RECENTE = '''
def calculer_heures_local(ligne):
    qte = float(ligne.get("qte") or 0)
    production = float(ligne.get("production_valeur") or 0)
    if production > 0:
        return round((qte / production) * 10) / 10
    h = float(ligne.get("heures") or 0)
    override = ligne.get("heures_manuelles") is True and abs(h) > 1e-9
    return h if override else qte
'''

APPELANT_LEGITIME = '''
from modules.aggregates import heures_effectives


def to_aggregate_line(ln):
    qte = float(ln.get("qte") or 0)
    heures = heures_effectives(ln.get("unite"), ln.get("heures"), ln.get("heures_manuelles"), qte, ln.get("production_valeur"))
    return {"heures": heures}
'''


# ── 1. Signaux d'analyse (fonctions pures) ────────────────────────────────
def test_signal_a_detecte_le_litteral_heure_units_copie_colle():
    assert trouve_litteral_unites_heures(COPIE_HISTORIQUE) != -1


def test_signal_b_detecte_le_triptyque_avec_arithmetique():
    assert trouve_triade_champs_avec_arithmetique(COPIE_RECENTE) != -1


def test_appelant_legitime_delegue():
    assert delegue(APPELANT_LEGITIME) is True


def test_analyser_fichier_ne_signale_pas_lappelant_legitime():
    assert analyser_fichier("modules/exemple_appelant.py", APPELANT_LEGITIME) is None


def test_analyser_fichier_exempte_la_source_canonique_meme_avec_signal_a():
    assert analyser_fichier("modules/aggregates.py", COPIE_HISTORIQUE) is None


def test_est_canonique_reconnait_le_fichier_source():
    assert est_canonique("modules/aggregates.py") is True


def test_analyser_fichier_detecte_la_copie_historique_hors_liste_blanche():
    assert analyser_fichier("modules/une_fausse_copie.py", COPIE_HISTORIQUE) is not None


def test_analyser_fichier_detecte_la_copie_recente_hors_liste_blanche():
    assert analyser_fichier("modules/une_autre_fausse_copie.py", COPIE_RECENTE) is not None


# ── 2. Bout en bout — vrai fichier temporaire, via scanner() ──────────────
def test_scanner_detecte_une_copie_injectee_dans_un_vrai_fichier_temporaire(tmp_path):
    dossier = tmp_path / "modules_faux"
    dossier.mkdir()
    fichier = dossier / "copie_suspecte.py"
    fichier.write_text(COPIE_HISTORIQUE, encoding="utf-8")

    suspects = scanner([str(dossier)])

    assert len(suspects) == 1
    chemin, ligne, motif = suspects[0]
    assert "copie_suspecte.py" in chemin
    assert ligne >= 1
    assert "_HEURE_UNITS" in motif or "HEURE_UNITS" in motif


def test_scanner_detecte_les_deux_signatures_dans_deux_fichiers_injectes(tmp_path):
    dossier = tmp_path / "modules_faux"
    dossier.mkdir()
    (dossier / "copie_a.py").write_text(COPIE_HISTORIQUE, encoding="utf-8")
    (dossier / "copie_b.py").write_text(COPIE_RECENTE, encoding="utf-8")
    (dossier / "appelant_legitime.py").write_text(APPELANT_LEGITIME, encoding="utf-8")

    suspects = scanner([str(dossier)])

    fichiers_suspects = {chemin for chemin, _, _ in suspects}
    assert len(suspects) == 2, f"attendu 2 suspects (A+B), obtenu {suspects}"
    assert any("copie_a.py" in f for f in fichiers_suspects)
    assert any("copie_b.py" in f for f in fichiers_suspects)
    assert not any("appelant_legitime.py" in f for f in fichiers_suspects), "pas de faux positif sur du code qui délègue"


def test_scanner_reste_vert_sur_le_perimetre_reel_modules_et_api():
    """Non-régression : le contrôle réel (périmètre de production) est vert
    aujourd'hui — modules/aggregates.py est canonique, ad_budget_api.py et
    budget_fingerprint.py délèguent déjà tous les deux."""
    import os

    from tools.verifier_source_unique import RACINE, _dossiers_par_defaut

    dossiers, fichiers = _dossiers_par_defaut()
    suspects = scanner(dossiers, fichiers)
    assert suspects == [], f"le périmètre réel devrait être vert : {suspects}"
    # Garde-fou du test lui-même : si RACINE ne pointe plus vers le dépôt (ex.
    # exécuté depuis un mauvais cwd), modules/ n'existerait pas et scanner()
    # renverrait [] pour une MAUVAISE raison (silence, pas un vrai vert).
    assert os.path.isdir(os.path.join(RACINE, "modules"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
