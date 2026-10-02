# -*- coding: utf-8 -*-
"""TEMOIN du garde-fou tools/verifier_tests_cables.py.

Un garde-fou qu'on n'a jamais vu mordre est un garde-fou dont on ne sait rien
(PC3, 1er oct 2026). Ce temoin le fait mordre sur de faux depots, cas par cas,
y compris sur le piege trouve au premier essai : le mot nu « tests » (le nom
du job « tests: ») couvrait tout le banc.
"""
import os
import shutil
import sys
import tempfile

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "tools"))

import verifier_tests_cables as V  # noqa: E402


def _depot(fichiers, workflow):
    d = tempfile.mkdtemp(prefix="garde-")
    os.makedirs(os.path.join(d, "tests"))
    os.makedirs(os.path.join(d, ".github", "workflows"))
    for f in fichiers:
        open(os.path.join(d, f), "w").close()
    with open(os.path.join(d, ".github", "workflows", "ci.yml"), "w", encoding="utf-8") as fh:
        fh.write(workflow)
    return d


def _verifier(fichiers, workflow, exclusions=None):
    d = _depot(fichiers, workflow)
    sauv = V.EXCLUSIONS
    V.EXCLUSIONS = dict(exclusions or {})
    try:
        return V.verifier(d)
    finally:
        V.EXCLUSIONS = sauv
        shutil.rmtree(d, ignore_errors=True)


WF = "jobs:\n  tests:\n    steps:\n      - run: python tests/test_a.py\n"


def test_un_fichier_cite_par_son_chemin_passe():
    assert _verifier(["tests/test_a.py"], WF) == ([], [])


def test_un_fichier_jamais_cite_mord():
    orphelins, _ = _verifier(["tests/test_a.py", "tests/test_zzz_jamais_cite.py"], WF)
    assert orphelins == ["tests/test_zzz_jamais_cite.py"]


def test_le_nom_du_job_tests_ne_couvre_rien():
    # Piege du premier essai : « tests: » est le nom du job, pas une collecte.
    orphelins, _ = _verifier(["tests/test_b.py"], "jobs:\n  tests:\n    steps: []\n")
    assert orphelins == ["tests/test_b.py"]


def test_un_commentaire_ne_cite_pas():
    wf = WF + "# NE TOURNE PAS ICI : tests/test_b.py\n      - run: echo x  # tests/test_c.py\n"
    orphelins, _ = _verifier(["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"], wf)
    assert orphelins == ["tests/test_b.py", "tests/test_c.py"]


def test_un_motif_et_un_dossier_couvrent():
    assert _verifier(["tests/test_a.py", "tests/test_b.py"],
                     "      - run: python -m pytest tests/test_*.py\n")[0] == []
    assert _verifier(["tests/test_a.py", "tests/check_x.py"],
                     "      - run: python -m pytest tests/\n")[0] == []


def test_une_exclusion_nommee_passe_et_une_perimee_mord():
    assert _verifier(["tests/test_a.py", "tests/test_b.py"], WF,
                     {"tests/test_b.py": "raison"}) == ([], [])
    _, perimees = _verifier(["tests/test_a.py"], WF,
                            {"tests/test_a.py": "deja cite", "tests/test_disparu.py": "x"})
    assert [f for f, _ in perimees] == ["tests/test_a.py", "tests/test_disparu.py"]


def test_le_vrai_depot_est_propre():
    orphelins, perimees = V.verifier(RACINE)
    assert orphelins == [] and perimees == [], (orphelins, perimees)


def test_chaque_exclusion_a_une_raison():
    for f, raison in V.EXCLUSIONS.items():
        assert raison and raison.strip(), f"{f} : exclusion sans raison"


if __name__ == "__main__":
    echecs = 0
    for nom, fn in sorted(globals().items()):
        if nom.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", nom)
            except Exception as e:
                echecs += 1
                print("FAIL", nom, ":", repr(e))
    print("---", "0 echec" if not echecs else "%d echec(s)" % echecs)
    sys.exit(1 if echecs else 0)
