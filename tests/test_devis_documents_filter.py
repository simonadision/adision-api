"""Test unitaire — filtre « Documents consultés » du devis (Ad BUD).

Motif (Simon 2026-08-18) : la liste ne doit afficher QUE les documents des
catégories GED Plans / Devis / Addenda de l'arborescence du projet. Avant ce
correctif, `_flatten_doc_names` excluait par liste NOIRE ("soumission",
"soumissions") — mais la migration 041 avait renommé la catégorie en
"Soumission sous-traitants" et ajouté "Dépôt de soumission VFS" (qui porte
les documents d'entreprise partagés : attestations, certificats,
résolutions...). Ni l'une ni l'autre ne matchait plus la liste noire : tous
ces documents internes/administratifs fuitaient dans le devis envoyé au
client. Le correctif inverse en liste BLANCHE (_DEVIS_ALLOWED_CATEGORY_NAMES
= {"plans", "devis", "addenda"}).

Fixture `_arbre_ged_realiste()` reproduit la forme exacte de l'arbre renvoyé
par GET /api/projects/{id}/documents (categories > disciplines > documents),
avec les six catégories pré-construction seedées par les migrations 030/040/
041 : Visite de chantier, Plans, Devis, Addenda, Soumission sous-traitants,
Dépôt de soumission VFS — cette dernière avec la discipline org-partagée
« Document de dépôt entreprise » (project_id NULL) qui porte les documents
observés dans le bug (attestation, certificat, résolution, étiquette).

Lancer :  python tests/test_devis_documents_filter.py     (autonome)
     ou :  pytest tests/test_devis_documents_filter.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.ad_devis_api import _flatten_doc_names, _DEVIS_ALLOWED_CATEGORY_NAMES  # noqa: E402


def _cat(name, disciplines=None, documents_uncategorized=None):
    return {
        "name": name,
        "disciplines": disciplines or [],
        "documents_uncategorized": [{"display_name": d} for d in (documents_uncategorized or [])],
    }


def _disc(name, documents):
    return {"name": name, "documents": [{"display_name": d} for d in documents]}


def _arbre_ged_realiste():
    """Reproduit les 6 catégories pré-construction (migrations 030/040/041)
    d'un projet Contracta réel, avec le contenu observé dans le bug."""
    return {
        "categories": [
            _cat("Visite de chantier", documents_uncategorized=["Rapport visite 2026-08-10.pdf"]),
            _cat("Plans", disciplines=[
                _disc("Architecture", ["A-100 Plan implantation.pdf", "A-200 Élévations.pdf"]),
                _disc("Structure", ["S-100 Fondations.pdf"]),
            ]),
            _cat("Devis", disciplines=[
                _disc("Architecture", ["Devis architectural 03.docx"]),
            ]),
            _cat("Addenda", disciplines=[
                _disc("Appel d'offres", ["Addenda 1.pdf", "Addenda 2.pdf"]),
            ]),
            # Renommée par la migration 041 — ne matchait plus l'ancienne
            # liste noire ("soumission"/"soumissions").
            _cat("Soumission sous-traitants", disciplines=[
                _disc("0000-BSDQ", ["SOMMAIRE BSDQ.PDF"]),
                _disc("07-Isolation et étanchéité", ["68845$-ISOLATION ARCTIQUE.pdf"]),
            ]),
            # Catégorie ajoutée par la migration 041, jamais couverte par
            # l'ancienne liste noire. La discipline "Document de dépôt
            # entreprise" (project_id NULL, org-partagée, mig 166) porte les
            # documents de conformité — jamais destinés au client.
            _cat("Dépôt de soumission VFS", disciplines=[
                _disc("Document de dépôt entreprise", [
                    "RÉSOLUTION-SIMON.XLSX",
                    "ATTESTATION RELATIVE À LA PROBITÉ DU SOUMISSIONNAIRE CONTRACTA.DOCX",
                    "ÉTIQUETTE D'ENVELOPPE CONTRACTA.DOCX",
                    "CERTIFICAT DE CONFORMITÉ ENTREPRISE.PDF",
                ]),
            ]),
        ]
    }


# ── Tests ────────────────────────────────────────────────────────────────
def test_seuls_plans_devis_addenda_sont_retenus():
    docs = _flatten_doc_names(_arbre_ged_realiste())
    attendu = {
        "A-100 Plan implantation.pdf", "A-200 Élévations.pdf", "S-100 Fondations.pdf",
        "Devis architectural 03.docx", "Addenda 1.pdf", "Addenda 2.pdf",
    }
    assert set(docs) == attendu, f"liste inattendue : {sorted(docs)}"
    print(f"  [OK] {len(docs)} documents retenus, tous Plans/Devis/Addenda")


def test_documents_administratifs_internes_exclus():
    docs = set(_flatten_doc_names(_arbre_ged_realiste()))
    fuites = {
        "SOMMAIRE BSDQ.PDF",
        "68845$-ISOLATION ARCTIQUE.pdf",
        "RÉSOLUTION-SIMON.XLSX",
        "ATTESTATION RELATIVE À LA PROBITÉ DU SOUMISSIONNAIRE CONTRACTA.DOCX",
        "ÉTIQUETTE D'ENVELOPPE CONTRACTA.DOCX",
        "CERTIFICAT DE CONFORMITÉ ENTREPRISE.PDF",
        "Rapport visite 2026-08-10.pdf",
    }
    encore_presentes = fuites & docs
    assert not encore_presentes, f"documents internes toujours présents : {sorted(encore_presentes)}"
    print("  [OK] aucun document Soumission sous-traitants / Dépôt VFS / Visite de chantier ne fuite")


def test_comparaison_insensible_casse_et_espaces():
    tree = {"categories": [_cat("  PLANS  ", disciplines=[_disc("Civil", ["plan-civil.pdf"])])]}
    docs = _flatten_doc_names(tree)
    assert docs == ["plan-civil.pdf"], f"catégorie 'PLANS' (casse/espaces) non reconnue : {docs}"
    print("  [OK] 'PLANS' (majuscules, espaces) reconnue comme 'Plans'")


def test_documents_uncategorized_dune_categorie_autorisee_inclus():
    tree = {"categories": [_cat("Devis", documents_uncategorized=["Devis sans discipline.pdf"])]}
    docs = _flatten_doc_names(tree)
    assert docs == ["Devis sans discipline.pdf"]
    print("  [OK] document sans discipline sous Devis toujours inclus")


def test_arbre_vide_ou_absent_ne_casse_pas():
    assert _flatten_doc_names(None) == []
    assert _flatten_doc_names({}) == []
    assert _flatten_doc_names({"categories": []}) == []
    print("  [OK] arbre None/vide -> liste vide, pas d'exception")


def test_liste_blanche_ne_contient_que_les_trois_dossiers_projet():
    # Garde-fou : si quelqu'un élargit la liste blanche par erreur (ex. en y
    # ajoutant 'soumission'), ce test rougit et le nomme explicitement.
    assert _DEVIS_ALLOWED_CATEGORY_NAMES == {"plans", "devis", "addenda"}, (
        f"_DEVIS_ALLOWED_CATEGORY_NAMES a changé : {_DEVIS_ALLOWED_CATEGORY_NAMES}")
    print("  [OK] _DEVIS_ALLOWED_CATEGORY_NAMES == {plans, devis, addenda}")


# ── Runner autonome ───────────────────────────────────────────────────────
def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"  [X] {t.__name__} : {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests OK")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
