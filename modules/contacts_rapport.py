"""PERSONNES IMPRIMÉES DANS LE BLOC « ENTREPRENEUR » (1er oct. 2026).

Simon : « dans les rapports il faudrait pouvoir choisir le contact à publier.
Tout vit dans le hub, mais il y a plusieurs ressources : entrepreneur général,
préconstruction, construction, administration […] une option qui nous permet
de choisir qui afficher sur le rapport ». Décisions : PLUSIEURS personnes
cochées, choix PAR PROJET dans le hub (migration 195 d'adision-app-api).

L'identité (hub_service.map_project_to_identity) porte `contacts_entrepreneur` :
une liste ordonnée de {role, nom, fonction, email, telephone}, où role vaut
"principal" (la personne ressource de l'entrepreneur général, champs
entrepreneur_pr_*) ou la catégorie de la personne (preconstruction,
construction, administration, ou vide).

RÈGLES D'IMPRESSION — identiques dans les deux moteurs (reportlab ici,
jsPDF dans packages/report-pdf/src/contactsRapport.js et le devis) :
  - la personne PRINCIPALE garde ses lignes d'avant, inchangées (« Contact
    entrepreneur », Fonction si renseignée, Courriel, Téléphone) — chaque
    appelant les écrit comme avant, avec ses replis ;
  - chaque personne EN PLUS : « <Section> : <nom> », puis Fonction, Courriel
    et Téléphone SEULEMENT s'ils sont renseignés ;
  - identité sans la clé (instantané d'avant la migration 195, hub ancien) :
    comportement d'avant, la principale seule.
"""

LIBELLES_SECTION = {
    "preconstruction": "Préconstruction",
    "construction": "Construction",
    "administration": "Administration",
}


def _liste(ident):
    lst = (ident or {}).get("contacts_entrepreneur")
    return lst if isinstance(lst, list) else None


def principal_affiche(ident) -> bool:
    """La personne principale s'imprime-t-elle ? Oui si la liste est absente
    (identité d'avant le choix), sinon seulement si elle y figure."""
    lst = _liste(ident)
    if lst is None:
        return True
    return any((c or {}).get("role") == "principal" for c in lst)


def contacts_supplementaires(ident) -> list:
    """Les personnes cochées en plus de la principale, dans l'ordre du hub."""
    lst = _liste(ident) or []
    return [c for c in lst if (c or {}).get("role") != "principal"]


def libelle_section(contact) -> str:
    return LIBELLES_SECTION.get((contact or {}).get("role") or "", "Contact")


def lignes_supplementaires(contacts, suffixe: str = "") -> list:
    """[(libellé, valeur)] pour les personnes en plus. `suffixe` : « : »
    pour le devis, qui écrit ses libellés avec les deux-points."""
    out = []
    for c in contacts or []:
        out.append((libelle_section(c) + suffixe, (c.get("nom") or "").strip() or "—"))
        for cle, lib in (("fonction", "Fonction"), ("email", "Courriel"), ("telephone", "Téléphone")):
            v = (c.get(cle) or "").strip() if isinstance(c.get(cle), str) else c.get(cle)
            if v:
                out.append((lib + suffixe, v))
    return out


# CHOIX AU MOMENT DU RAPPORT (2 oct. 2026, Simon : « je veux pouvoir choisir
# qui on met dans le rapport. Préconstruction, construction… », puis, entre
# « par phase », « au moment du rapport » et « les deux » : « au moment du
# rapport »). L'aperçu envoie la liste des personnes cochées POUR CE RAPPORT ;
# elle remplace, pour ce rendu seulement, la liste cochée dans le hub. Rien
# n'est écrit : ni la fiche hub, ni l'instantané d'identité du budget.

_CHAMPS = ("role", "nom", "fonction", "email", "telephone")
_MAX_PERSONNES = 30
_MAX_CAR = 200


def contacts_depuis_param(texte):
    """Liste normalisée des personnes choisies, ou None si le paramètre est
    absent ou illisible (on retombe alors sur le choix du hub). Une liste
    VIDE est un choix valide : personne dans le bloc."""
    if texte in (None, ""):
        return None
    import json
    try:
        brut = json.loads(texte)
    except (TypeError, ValueError):
        return None
    if not isinstance(brut, list):
        return None
    out = []
    for c in brut[:_MAX_PERSONNES]:
        if not isinstance(c, dict):
            continue
        out.append({k: (str(c.get(k)).strip()[:_MAX_CAR] if c.get(k) not in (None, "") else None)
                    for k in _CHAMPS})
    return out
