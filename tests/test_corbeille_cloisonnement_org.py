# -*- coding: utf-8 -*-
"""TEMOIN -- les 3 routes de la corbeille d'Ad BUD sont cloisonnees par organisation.

CE QUI ETAIT OUVERT, ET C'ETAIT MA FAUTE. Les trois routes /admin/* de la
corbeille ne portaient AUCUN filtre d'organisation. Un gestionnaire (org_role
= 'admin') de n'importe quelle entreprise cliente pouvait :
  - LIRE la corbeille de toutes les autres, avec le nom du projet et celui du
    client (hub_identity_snapshot) ;
  - RESTAURER le projet supprime d'une autre entreprise ;
  - le SUPPRIMER DEFINITIVEMENT -- DELETE physique, CASCADE sur budget_lignes,
    devis et lots. Aucune recuperation.

L'ASYMETRIE ETAIT LA PREUVE. `delete_projet` -- METTRE un projet a la corbeille
-- appelait `_load_and_authorize_projet`. En SORTIR un, ou le detruire, ne
passait par rien. On ne pouvait jeter que les siens, mais agir sur ceux de
tout le monde. Regle de Simon, 1er oct 2026 : « tu me corrige tous de suite et
tu bloque cette manoeuvre ».

MESURE DU 2 OCT 2026 (connecteurs postgres-bud et postgres-hub, lecture seule) :
  2 organisations, 27 projets, 4 a la corbeille, 0 projet sans organisation ;
  2 comptes org_role='admin', dont 1 deja super_admin -> UN SEUL compte
  franchissait la frontiere sans y avoir droit. Zero compte 'staff'.

ON LIT LE SOURCE, et c'est voulu : rejouer ces routes demanderait un serveur,
une base Ad BUD et trois JWT d'organisations differentes. Ce banc verrouille
la PROPRIETE STRUCTURELLE -- qu'aucune des trois routes ne retourne a l'etat
ou elle ne consulte ni l'organisation ni une garde.

AUTONOME DANS SA LECTURE : il ne demande ni base, ni reseau, ni fastapi. Il
est en revanche CITE DANS ci.yml -- ce depot cable ses bancs un par un, et un
banc orphelin est pire qu'un banc absent (lecon du 1er oct, 27 sur 49).
"""
import ast
import io
import os
import re

import pytest

_RACINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MODULE = os.path.join(_RACINE, "modules", "ad_budget_api.py")

# Les trois routes reparees. Le DECORATEUR, pas le nom de fonction : c'est ce
# que voit l'appelant, et c'est ce qui ne doit pas bouger.
ROUTES_CORBEILLE = {
    ("GET", "/admin/projets-supprimes"),
    ("POST", "/admin/projets/{projet_id}/restaurer"),
    ("DELETE", "/admin/projets/{projet_id}/definitif"),
}

# Les CINQ formes de cloisonnement reellement employees dans ce module. La
# liste a ete etablie en lisant les 30 routes qui touchent ad_budget.projets,
# pas devinee : ma PREMIERE version n'en connaissait que trois et accusait a
# tort /projets/mine, qui filtre par `user_id = %s` -- plus strict que l'org.
# Un outil a faux positifs ne sera plus jamais ouvert.
FORMES_DE_CLOISONNEMENT = (
    "_load_and_authorize_projet",
    "_authorize_projet",
    "_projets_scope_where",
    "_corbeille_scope_where",
    "organization_id",
)
_RE_PAR_UTILISATEUR = re.compile(r"user_id\s*=\s*%s")


def _source():
    return io.open(MODULE, encoding="utf-8").read()


def _sans_commentaires_ni_docstring(noeud, src):
    """Le corps d'une fonction, prive de ses commentaires ET de sa docstring.

    INDISPENSABLE, et je l'ai appris deux fois a mes depens : mes propres
    commentaires CITENT les mots que je cherche. Un banc qui lit le source
    brut passe au vert parce que l'explication de la regle contient la regle
    -- un banc vert dont le sujet a disparu.
    """
    corps = list(noeud.body)
    if (corps and isinstance(corps[0], ast.Expr)
            and isinstance(corps[0].value, ast.Constant)
            and isinstance(corps[0].value.value, str)):
        corps = corps[1:]                      # la docstring saute
    morceaux = [ast.get_source_segment(src, n) or "" for n in corps]
    texte = "\n".join(morceaux)
    return "\n".join(l for l in texte.splitlines() if not l.strip().startswith("#"))


def _routes(src):
    """{(METHODE, chemin): corps nettoye} pour toute route @router.*."""
    arbre = ast.parse(src)
    trouvees = {}
    for n in ast.walk(arbre):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in n.decorator_list:
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)):
                continue
            cible = d.func.value
            if not (isinstance(cible, ast.Name) and cible.id == "router"):
                continue
            if not (d.args and isinstance(d.args[0], ast.Constant)):
                continue
            trouvees[(d.func.attr.upper(), d.args[0].value)] = \
                _sans_commentaires_ni_docstring(n, src)
    return trouvees


def _est_cloisonnee(corps):
    return (any(f in corps for f in FORMES_DE_CLOISONNEMENT)
            or _RE_PAR_UTILISATEUR.search(corps) is not None)


# ══════════════════════════════════════════════════════════════════════════
# ANTI-VACUITE : le detecteur doit savoir dire OUI *et* NON.
#
# Regle de PC1, apprise le 1er oct : tout test d'ABSENCE porte un temoin de
# PRESENCE dans le MEME ensemble. Ici, les deux sens sont verrouilles -- sans
# ca, un detecteur casse (qui repondrait toujours « cloisonnee ») rendrait
# TOUS les cas verts sans que rien ne soit verifie.
# ══════════════════════════════════════════════════════════════════════════

def test_le_detecteur_sait_dire_NON():
    """Temoin NEGATIF : une route sans aucune garde DOIT etre vue comme nue."""
    nue = ('conn = get_conn()\n'
           'cur = conn.cursor()\n'
           'cur.execute("SELECT id FROM ad_budget.projets WHERE supprime_le IS NOT NULL")\n')
    assert not _est_cloisonnee(nue), \
        "le detecteur declare cloisonnee une route qui ne l'est pas : tous les cas mentent"


def test_le_detecteur_sait_dire_OUI():
    """Temoin POSITIF, pris dans le MEME ensemble : `delete_projet` -- la route
    qui MET a la corbeille -- a toujours ete cloisonnee. Si le detecteur ne la
    reconnait plus, il ne reconnait plus rien."""
    routes = _routes(_source())
    corps = routes.get(("DELETE", "/projets/{projet_id}"))
    assert corps is not None, "DELETE /projets/{projet_id} a disparu : l'ancre du temoin n'existe plus"
    assert _est_cloisonnee(corps), \
        "le temoin positif n'est plus vu comme cloisonne : le detecteur est casse"


def test_les_TROIS_routes_de_corbeille_existent_encore():
    """Sans ce cas, renommer une route rendrait les suivants verts PAR VACUITE
    -- ils boucleraient sur un ensemble vide."""
    presentes = set(_routes(_source())) & ROUTES_CORBEILLE
    manquantes = ROUTES_CORBEILLE - presentes
    assert not manquantes, (
        "route(s) de corbeille introuvable(s) : %s. Si elles ont ete renommees, "
        "mets ce banc a jour -- ne le laisse pas passer au vert sur du vide." % sorted(manquantes))


# ══════════════════════════════════════════════════════════════════════════
# LE CAS QUI COMPTE.
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("methode,chemin", sorted(ROUTES_CORBEILLE))
def test_chaque_route_de_corbeille_est_cloisonnee(methode, chemin):
    corps = _routes(_source())[(methode, chemin)]
    assert _est_cloisonnee(corps), (
        "%s %s ne consulte NI l'organisation NI une garde d'autorisation.\n"
        "C'est le trou du 2 oct 2026 : un gestionnaire de n'importe quelle "
        "entreprise voyait, restaurait et DETRUISAIT DEFINITIVEMENT les projets "
        "supprimes de toutes les autres." % (methode, chemin))


def test_AUCUNE_route_du_module_touchant_projets_n_est_nue():
    """La propriete GENERALE, pas seulement les trois reparees -- sinon une
    QUATRIEME route nue pourrait naitre a cote sans que rien ne la voie.
    Mesure du 2 oct : 30 routes touchent ad_budget.projets, 0 est nue."""
    nues = sorted(f"{m} {p}" for (m, p), c in _routes(_source()).items()
                  if "ad_budget.projets" in c and not _est_cloisonnee(c))
    assert not nues, "route(s) sans cloisonnement : %s" % nues


def test_la_liste_NE_REOUVRE_PAS_le_trou_par_la_clause_de_compatibilite():
    """`OR organization_id IS NULL` existe ailleurs dans ce module (compat des
    projets d'avant le multi-tenant). Dans une CORBEILLE, cette clause rendrait
    un projet sans organisation visible par TOUS les gestionnaires de TOUTES
    les entreprises -- exactement le trou qu'on vient de boucher.
    Mesure du 2 oct : 0 projet sur 27 a un organization_id NULL."""
    corps = _routes(_source())[("GET", "/admin/projets-supprimes")]
    aplati = " ".join(corps.split()).upper()
    assert "ORGANIZATION_ID IS NULL" not in aplati, \
        "la clause de compatibilite rouvre la corbeille a toutes les organisations"


def test_la_liste_montre_TOUJOURS_la_corbeille_et_rien_d_autre():
    """On securise sans casser : la liste doit continuer de ne montrer QUE les
    projets supprimes."""
    corps = _routes(_source())[("GET", "/admin/projets-supprimes")]
    assert "supprime_le IS NOT NULL" in corps, \
        "la liste ne borne plus sur la corbeille : elle montrerait les projets VIVANTS"


# ══════════════════════════════════════════════════════════════════════════
# LE HELPER, EPROUVE POUR DE VRAI -- pas seulement lu.
#
# Il n'a aucune dependance (de simples acces a un dict) : on extrait sa source
# et on l'EXECUTE. Ca verifie son COMPORTEMENT, pas la presence de mots-cles.
# Importer le module entier demanderait fastapi, psycopg et des variables
# d'environnement ; ce banc resterait alors rouge pour une raison etrangere a
# ce qu'il mesure.
# ══════════════════════════════════════════════════════════════════════════

def _helper():
    src = _source()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == "_corbeille_scope_where":
            espace = {}
            exec(ast.get_source_segment(src, n), espace)  # noqa: S102
            return espace["_corbeille_scope_where"]
    raise AssertionError("_corbeille_scope_where a disparu du module")


def test_un_gestionnaire_est_borne_a_SON_organisation():
    ou, args = _helper()({"platform_role": "client", "org_role": "admin",
                          "organization_id": "64c45c53-ff2b-40ce-b05d-fe0a3f5144c0"})
    assert "organization_id" in ou and "%s" in ou, "le WHERE ne porte pas l'organisation"
    assert args == ("64c45c53-ff2b-40ce-b05d-fe0a3f5144c0",)
    assert ou.strip().upper() != "TRUE", "un gestionnaire voit TOUT : le trou est rouvert"


def test_seul_super_admin_franchit_la_frontiere():
    ou, args = _helper()({"platform_role": "super_admin", "organization_id": None})
    assert ou.strip().upper() == "TRUE" and args == ()


def test_un_perimetre_INDETERMINE_ne_vaut_PAS_universel():
    """LE CAS LE PLUS IMPORTANT DES TROIS. Un JWT sans organisation active ne
    doit PAS ouvrir toute la corbeille. 'FALSE' = corbeille vide. Si un jour
    quelqu'un remplace ce FALSE par TRUE « pour que ca marche », ce cas rougit."""
    ou, args = _helper()({"platform_role": "client", "org_role": "admin",
                          "organization_id": None})
    assert ou.strip().upper() == "FALSE", \
        "sans organisation, la corbeille s'ouvre en grand au lieu de rester vide"
    assert args == ()


if __name__ == "__main__":
    import traceback
    cas, rouges = 0, 0
    for nom, f in sorted(globals().items()):
        if not nom.startswith("test_"):
            continue
        jeux = ([(m, c) for m, c in sorted(ROUTES_CORBEILLE)]
                if nom == "test_chaque_route_de_corbeille_est_cloisonnee" else [()])
        for jeu in jeux:
            cas += 1
            try:
                f(*jeu)
                print(f"  OK  {nom}{jeu if jeu else ''}")
            except AssertionError:
                rouges += 1
                print(f"  ROUGE  {nom}{jeu if jeu else ''}")
                traceback.print_exc()
    print(f"{cas} cas, {rouges} rouge(s)")
    raise SystemExit(1 if rouges else 0)
