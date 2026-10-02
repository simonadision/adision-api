"""Personnes imprimées dans le bloc ENTREPRENEUR (1er oct. 2026, Simon :
« choisir le contact à publier » — plusieurs personnes, choix par projet dans
le hub, migration 195 d'adision-app-api). Cf. modules/contacts_rapport.py."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reportlab.platypus import SimpleDocTemplate  # noqa: E402

from modules.contacts_rapport import (  # noqa: E402
    principal_affiche, contacts_supplementaires, lignes_supplementaires,
)
from modules.hub_service import map_project_to_identity  # noqa: E402
from modules.ad_budget_api import _build_client_entrepreneur_header  # noqa: E402

PROJET_HUB = {
    "name": "Patinoire", "entrepreneur_nom": "Contracta",
    "entrepreneur_pr_nom": "Pierre-Olivier Vézina", "entrepreneur_pr_fonction": "Président",
    "entrepreneur_pr_email": "po@contracta.ca", "entrepreneur_pr_telephone": "4189310686",
}
SIMON = {"nom": "Simon Hachey", "fonction": "Directeur préconstruction", "courriel": None,
         "telephone": "4189310686", "categorie": "preconstruction"}


def _texte_entete(ident):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pageCompression=0)
    doc.build([_build_client_entrepreneur_header(500, ident, avec_dates=False)])
    return buf.getvalue().decode("latin-1")


def test_hub_sans_la_case_rien_ne_change():
    """Hub d'avant la migration 195 : la principale seule, comme avant."""
    ident = map_project_to_identity(PROJET_HUB)
    assert [c["role"] for c in ident["contacts_entrepreneur"]] == ["principal"]
    assert principal_affiche(ident) is True
    assert contacts_supplementaires(ident) == []


def test_identite_sans_la_cle_comportement_d_avant():
    """Instantané persisté avant la migration : pas de clé -> principale."""
    assert principal_affiche({"contact_entrepreneur": "X"}) is True
    assert contacts_supplementaires({"contact_entrepreneur": "X"}) == []


def test_personnes_cochees_dans_l_ordre():
    ident = map_project_to_identity({**PROJET_HUB, "contacts_rapports": [SIMON]})
    assert [c["role"] for c in ident["contacts_entrepreneur"]] == ["principal", "preconstruction"]
    assert lignes_supplementaires(contacts_supplementaires(ident)) == [
        ("Préconstruction", "Simon Hachey"),
        ("Fonction", "Directeur préconstruction"),
        ("Téléphone", "4189310686"),  # courriel vide : pas de ligne
    ]
    assert lignes_supplementaires(contacts_supplementaires(ident), " :")[0] == ("Préconstruction :", "Simon Hachey")


def test_principale_decochee():
    ident = map_project_to_identity({**PROJET_HUB, "entrepreneur_pr_sur_rapports": False,
                                     "contacts_rapports": [SIMON]})
    assert principal_affiche(ident) is False
    assert [c["role"] for c in ident["contacts_entrepreneur"]] == ["preconstruction"]


def test_le_rapport_serveur_imprime_les_personnes_choisies():
    avant = _texte_entete(map_project_to_identity(PROJET_HUB))
    assert "Vézina".encode("latin-1").decode("latin-1") in avant or "V\\351zina" in avant
    assert "Simon Hachey" not in avant
    assert "Contact entrepreneur" in avant  # témoin : sinon son absence plus bas ne prouverait rien
    apres = _texte_entete(map_project_to_identity({**PROJET_HUB, "contacts_rapports": [SIMON]}))
    assert "Simon Hachey" in apres
    seul = _texte_entete(map_project_to_identity({**PROJET_HUB, "entrepreneur_pr_sur_rapports": False,
                                                  "contacts_rapports": [SIMON]}))
    assert "Simon Hachey" in seul
    assert "Contact entrepreneur" not in seul


# ── Choix au moment du rapport (2 oct. 2026) ────────────────────────────────
from modules.contacts_rapport import contacts_depuis_param  # noqa: E402


def test_param_absent_ou_illisible_retombe_sur_le_hub():
    for t in (None, "", "pas du json", '{"role": "principal"}', "42"):
        assert contacts_depuis_param(t) is None


def test_liste_vide_est_un_choix_personne_dans_le_bloc():
    assert contacts_depuis_param("[]") == []


def test_choix_normalise_dans_l_ordre():
    t = ('[{"role": "construction", "nom": "  Olivier B. ", "email": "o@x.ca"},'
         ' {"role": "principal"}, 7, {"role": "preconstruction", "nom": "' + "x" * 300 + '"}]')
    choix = contacts_depuis_param(t)
    assert [c["role"] for c in choix] == ["construction", "principal", "preconstruction"]
    assert choix[0]["nom"] == "Olivier B." and choix[0]["telephone"] is None
    assert len(choix[2]["nom"]) == 200


def test_le_choix_pilote_l_impression():
    from modules.contacts_rapport import principal_affiche, contacts_supplementaires
    ident = {"contacts_entrepreneur": contacts_depuis_param('[{"role": "construction", "nom": "Olivier"}]')}
    assert principal_affiche(ident) is False
    assert [c["nom"] for c in contacts_supplementaires(ident)] == ["Olivier"]
