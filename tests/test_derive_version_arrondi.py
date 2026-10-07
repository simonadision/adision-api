"""derive_version : arrondi de la version, base relue, vrais totaux.

7 oct. 2026 (PC1, chantier attribué par PC3). Trois faits MESURÉS sur la
production avant d'écrire une ligne, que ce banc épingle :

1. PAS DE TOLÉRANCE sur la base. Elle est écrite par le serveur depuis le même
   float que le total et relue à l'identique (JSONB garde le texte, repr(float)
   fait l'aller-retour). Un seuil au demi-cent masquerait de VRAIES
   modifications. Mutant : arrondir la base à 2 décimales à l'écriture → une
   ligne à total non arrondi passe « modifiée » à tort, et ce banc le voit.
2. L'ARRONDI DE LA VERSION. Basculer `arrondi_dollar` après la version change
   les totaux jusqu'à 0,50 $ : la version doit se comparer à l'arrondi QU'ELLE
   A VU, et NOMMER la bascule (`arrondi_change`) au lieu de compter des lignes.
3. LES VRAIS TOTAUX. export-for-con donnait à derive_version la colonne
   `total` brute, qui ne porte que les MATÉRIAUX (V.1 du 290 : 474 lignes sur
   1 209 faussement « modifiées »). Le banc montre ce que cette erreur rend.

Banc PUR : import direct, aucune base, aucun FastAPI.
    python tests/test_derive_version_arrondi.py
"""
import json
import os
import sys

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RACINE)

from modules.ad_budget_api import derive_version  # noqa: E402
from modules.budget_fingerprint import _line_total  # noqa: E402

ECHECS = []


def _rouge(msg):
    ECHECS.append(msg)
    print("  ROUGE  " + msg)


def _vert(msg):
    print("  ok     " + msg)


# Une ligne à total NON ENTIER : 3 × 12,345 $ + 2 h × 41,10 $ + 100,25 $ ST
# = 37,035 + 82,20 + 100,25 = 219,485 $ ; arrondie au dollar : 219 $.
LIGNE = {"description": "Gypse", "unite": "pi2", "qte": 3, "prix_unitaire": 12.345,
         "heures": 2, "taux_horaire": 41.10, "sous_traitant_montant": 100.25}
# La COLONNE `total` d'une vraie ligne : les matériaux seuls (37,035 $).
COLONNE_TOTAL_MATERIAUX = 37.035


def _lignes(arrondi):
    d = dict(LIGNE)
    d["total"] = _line_total(arrondi, d)
    return {7: d}


def _facteurs(arrondi, base_ecrite=None):
    """Ce que le serveur ÉCRIT (ad_budget_api, _ecrire_version) puis relit
    du JSONB : float(Decimal(str(total))) → json → float."""
    total = _line_total(arrondi, LIGNE)
    base = base_ecrite(total) if base_ecrite else float(str(total))
    return json.loads(json.dumps({"7": {"facteur": 1.0, "base": base, "exclue": False}}))


def test_aucune_tolerance_la_base_relue_vaut_le_total():
    """Fait 1 : sans arrondi, total = 219,48500000000001 ; la base relue du
    JSON vaut exactement ce float, donc 0 ligne modifiée, SANS seuil."""
    d = derive_version(_lignes(False), _facteurs(False))
    if d["lignes_modifiees"] != 0:
        _rouge("base relue du JSON ≠ total : %s lignes modifiées sans changement"
               % d["lignes_modifiees"])
    else:
        _vert("base relue = total au float près, sans tolérance")
    # TÉMOIN : une vraie modification SOUS LE DEMI-CENT (0,004 $) doit se
    # voir — c'est elle qu'un seuil « au cas où » masquerait.
    lignes = _lignes(False)
    lignes[7]["total"] = lignes[7]["total"] + 0.004
    if derive_version(lignes, _facteurs(False))["lignes_modifiees"] != 1:
        _rouge("une modification de 0,004 $ n'est pas vue : une tolérance la masque")
    else:
        _vert("témoin : une modification de 0,004 $ est comptée")


def test_MUTANT_base_arrondie_a_deux_decimales():
    """Si quelqu'un arrondit la base à l'écriture, la ligne à 219,485 $ passe
    « modifiée » sans que personne n'y ait touché. Ce banc doit le voir."""
    d = derive_version(_lignes(False), _facteurs(False, lambda t: round(t, 2)))
    if d["lignes_modifiees"] == 0:
        _rouge("le mutant « base arrondie à 2 décimales » passe inaperçu : "
               "ce banc n'épingle rien")
    else:
        _vert("mutant « base arrondie » : vu (%s ligne modifiée)" % d["lignes_modifiees"])


def test_arrondi_bascule_apres_la_version():
    """Fait 2 : version prise arrondi=true, projet passé à false."""
    lignes = _lignes(False)                       # l'écran, sans arrondi
    facteurs = _facteurs(True)                    # la base, vue arrondie (219 $)
    sans = derive_version(lignes, facteurs)       # appelant d'avant : ne sait pas
    if sans["lignes_modifiees"] != 1:
        _rouge("témoin : sans l'arrondi de la version, la ligne devrait sortir "
               "modifiée (sinon ce test ne prouve rien)")
    avec = derive_version(lignes, facteurs, arrondi_version=True, arrondi_projet=False)
    if avec["lignes_modifiees"] != 0:
        _rouge("arrondi basculé : %s ligne comptée modifiée alors que personne "
               "n'y a touché" % avec["lignes_modifiees"])
    else:
        _vert("arrondi basculé : 0 ligne modifiée")
    if not (avec["arrondi_change"] and avec["arrondi_de_la_version"] is True
            and avec["arrondi_du_projet"] is False):
        _rouge("la bascule n'est pas NOMMÉE : %r" % {k: avec[k] for k in
               ("arrondi_change", "arrondi_de_la_version", "arrondi_du_projet")})
    else:
        _vert("la bascule est nommée (arrondi_change, version=true, projet=false)")
    # L'écart reste celui de l'ÉCRAN : facteur 1 → 0.
    if avec["ecart_courant"] != 0:
        _rouge("écart courant ≠ 0 avec un facteur 1 : %s" % avec["ecart_courant"])
    # Une VRAIE modification reste visible même quand l'arrondi a basculé.
    lignes_mod = _lignes(False)
    lignes_mod[7]["qte"] = 4
    lignes_mod[7]["total"] = _line_total(False, lignes_mod[7])
    mod = derive_version(lignes_mod, facteurs, arrondi_version=True, arrondi_projet=False)
    if mod["lignes_modifiees"] != 1:
        _rouge("une vraie modification disparaît quand l'arrondi a basculé")
    else:
        _vert("une vraie modification reste comptée malgré la bascule")


def test_MUTANT_arrondi_ignore():
    """Si derive_version ignore l'arrondi de la version, le test ci-dessus
    doit rougir : on le vérifie en simulant l'appelant qui ne le passe pas."""
    d = derive_version(_lignes(False), _facteurs(True),
                       arrondi_version=None, arrondi_projet=False)
    if d["lignes_modifiees"] == 0:
        _rouge("mutant « arrondi ignoré » : 0 ligne modifiée, le banc ne le "
               "distingue pas du cas corrigé")
    else:
        _vert("mutant « arrondi ignoré » : vu (la ligne repasse modifiée)")
    if d["arrondi_change"] is not False or d["arrondi_de_la_version"] is not None:
        _rouge("arrondi inconnu : il doit rester None, jamais deviné")


def test_vrais_totaux_et_non_la_colonne_materiaux():
    """Fait 3 : la colonne `total` brute ne porte que les matériaux."""
    facteurs = _facteurs(True)
    bon = derive_version(_lignes(True), facteurs)
    if bon["lignes_modifiees"] != 0:
        _rouge("vrais totaux : %s ligne modifiée sans changement" % bon["lignes_modifiees"])
    else:
        _vert("vrais totaux (_line_total) : 0 ligne modifiée")
    brut = derive_version({7: dict(LIGNE, total=COLONNE_TOTAL_MATERIAUX)}, facteurs)
    if brut["lignes_modifiees"] != 1:
        _rouge("témoin : la colonne matériaux devrait faire sortir la ligne "
               "modifiée — sinon ce banc ne protège pas export-for-con")
    else:
        _vert("témoin : la colonne brute (matériaux seuls) la fait passer "
              "« modifiée » — le défaut corrigé dans export-for-con")


def test_export_for_con_passe_par_lignes_pour_version():
    """Le correctif lui-même : export-for-con ne doit plus donner les lignes
    brutes à derive_version. Garde textuelle sur le code."""
    src = open(os.path.join(_RACINE, "modules", "ad_budget_api.py"), encoding="utf-8").read()
    if 'derive_version(\n                    {r["id"]: r for r in budget_rows}' in src or \
       "{r[\"id\"]: r for r in budget_rows}, facteurs_version" in src:
        _rouge("export-for-con donne encore la colonne brute à derive_version")
    elif "_derive(_lignes_pour_version(cur, projet_id)," not in src:
        _rouge("export-for-con ne passe pas par _lignes_pour_version")
    else:
        _vert("export-for-con passe par _lignes_pour_version")


if __name__ == "__main__":
    for nom, f in list(globals().items()):
        if nom.startswith("test_") and callable(f):
            print(nom)
            f()
    if ECHECS:
        print("\n%d échec(s)" % len(ECHECS))
        sys.exit(1)
    print("\nderive_version : tout est vert")
