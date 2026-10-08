# -*- coding: utf-8 -*-
"""Le compte charte Q de CHAQUE NATURE d'une ligne de budget — résolu côté serveur.

C'EST UN PORT, PAS UNE INVENTION. L'original est
`adision-con-api/modules/correspondance_comptes.py` (origin/main 4c2d101,
PC4, 8 oct. 2026). Même ordre de résolution, même `_morceaux`, même cache,
même garde 036, même « ne lève jamais ». Ce qui DIFFÈRE est listé ici, et
nulle part ailleurs :

  (1) LES NATURES PRÉSENTES se lisent sur les MONTANTS D'AD BUD, pas sur des
      sous-totaux stockés (Ad CON a `mat_subtotal_origin`, `mat_real`…, Ad BUD
      n'a que les intrants). `montants_par_nature(ligne)` les calcule avec les
      MÊMES pièces que `budget_fingerprint._line_total` — `quantite_effective`
      et `heures_effectives` importées de modules.aggregates, JAMAIS recopiées
      (tools/verifier_source_unique.py). Une nature est présente si son
      montant est non nul (un crédit négatif compte).
  (2) LE CODE CSI est le champ `section` de la ligne (Ad CON : `csi_code`).
  (3) L'ADRESSE DU HUB vient de `hub_service.HUB_API_URL`, lue à l'appel
      (Ad CON : `hub_client._ad_hub_url()`). Même leçon que PC4 : une adresse
      recopiée dans ce module serait une supposition qui attend son heure.
  (4) Le compte unique d'avant s'appelle ici `compte_metier_force`
      (Ad CON : `compte_metier`).
  (5) Ad BUD n'a pas d'options par projet : `options_projet` reste accepté
      (signature identique) mais aucun appelant ne le passe aujourd'hui ; les
      interrupteurs `mo_vers_metier` / `st_vers_metier` viennent de la charge
      du hub, par organisation.

⚠ QUATRIÈME IMPLÉMENTATION DE LA MÊME RÈGLE — À FUSIONNER, PAS À MULTIPLIER.
Elle existe déjà dans adision-con-api (Python, l'original de ce port), dans
Ad EST (JS, `apps/ad-est/src/api/charteQ.js`) et dans le front d'Ad CON.
L'issue monorepo #1089 (PC3) prévoit UNE fonction JS dans packages/aggregates
et un fichier de cas partagé `packages/aggregates/cas/charte-q-natures.json`
— PAS ENCORE ÉCRITS au 8 oct. 2026. Quand ils existeront, le banc
tests/test_correspondance_comptes_bud.py devra lire ce fichier de cas au lieu
de porter les siens. D'ici là, quatre copies peuvent diverger en silence :
l'écran d'un module montrerait un compte, l'export d'un autre en enverrait
un deuxième.

L'ORDRE DE RÉSOLUTION (fixé par la migration 198 du hub, étendu le 8 oct.) :
  (a0) le compte ENREGISTRÉ pour la nature (`compte_mat|mo|st`,
       migrations/sprint_comptes_par_nature.sql) l'emporte TOUJOURS ;
  (a)  sinon l'ancien compte unique `compte_metier_force`, SEULEMENT si la
       ligne n'a qu'une nature, ou pour le MATÉRIAU — on ne devine pas à
       laquelle de plusieurs natures il appartenait ;
  (b)  sinon la paire (code CSI, nature) au PLUS LONG PRÉFIXE posé ;
  (b') sinon, pour MO / ST et si l'interrupteur `mo_vers_metier` /
       `st_vers_metier` est levé, la paire (code CSI, MAT) — le compte du
       métier ;
  (c)  sinon la règle de NATURE (`regles_nature`) ;
  (d)  sinon None -> « non associé », et ça y RESTE. Jamais de seau, jamais
       de compte deviné : un compte absent se voit, un compte faux a l'air juste.

NE LÈVE JAMAIS. Si le hub est muet, on sert le cache même périmé ; sans cache,
None, et toutes les lignes tombent en « non associé ». Une panne du hub ne doit
jamais faire inventer un compte, ni casser l'export vers Ad CON.
"""
import json
import logging
import time
import urllib.error  # noqa: F401 — même surface que l'original (bancs)
import urllib.request

from modules import hub_service
from modules.aggregates import heures_effectives, quantite_effective

logger = logging.getLogger("correspondance_comptes")

NATURES = ("MAT", "MO", "ST")
# La colonne où chaque nature est ENREGISTRÉE (migration sprint_comptes_par_nature).
COLONNE_NATURE = {"MAT": "compte_mat", "MO": "compte_mo", "ST": "compte_st"}

_TTL_SECONDS = 300
_FETCH_TIMEOUT_SECONDS = 3.0
_CHEMIN = "/api/codification/correspondance-comptes"

# {organization_id: (pose_a, charge_utile)}
_CACHE = {}


def _url_hub():
    """L'adresse du hub vient de `hub_service`, lue À L'APPEL — jamais d'une
    constante locale (voir la différence (3) en tête de module)."""
    return hub_service.HUB_API_URL


def _nombre(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    return n if n == n and n not in (float("inf"), float("-inf")) else 0.0


def montants_par_nature(ligne):
    """{"MAT": x, "MO": y, "ST": z} — les trois blocs d'une ligne d'Ad BUD.

    LES MÊMES PIÈCES QUE `budget_fingerprint._line_total` (la source unique du
    total de ligne), découpées par nature au lieu d'être additionnées :
      MAT = quantite_effective(qte, unite, qte_facteur) × prix × (1 + ajust_mat/100)
      MO  = heures_effectives(...) × taux × (1 + ajust_mo/100)
      ST  = sous_traitant_montant × (1 + ajust_st/100), et 0 si qte <= 0
            (règle #2 de Simon, 17 août 2026 : QTÉ=0 exclut le montant ST).
    L'ajustement de LIGNE (`ajustement_pct`) multiplie les trois blocs : il ne
    fait ni apparaître ni disparaître une nature, sauf à -100, où la ligne
    entière vaut zéro — on ne l'applique donc pas ici.

    Ces montants ne servent QU'À dire quelles natures EXISTENT. Ils ne
    remplacent aucun total : pour un montant, c'est `_line_total` /
    `compute_budget_totals` qui font foi."""
    qte = _nombre(ligne.get("qte"))
    heures = heures_effectives(ligne.get("unite"), ligne.get("heures"),
                               ligne.get("heures_manuelles"), qte,
                               ligne.get("production_valeur"))
    mat = (quantite_effective(qte, ligne.get("unite"), ligne.get("qte_facteur"))
           * _nombre(ligne.get("prix_unitaire"))
           * (1 + _nombre(ligne.get("ajust_materiaux")) / 100))
    mo = (_nombre(heures) * _nombre(ligne.get("taux_horaire"))
          * (1 + _nombre(ligne.get("ajust_main_oeuvre")) / 100))
    st = (_nombre(ligne.get("sous_traitant_montant"))
          * (1 + _nombre(ligne.get("ajust_sous_traitant")) / 100)) if qte > 0 else 0.0
    return {"MAT": mat, "MO": mo, "ST": st}


def natures_de_la_ligne(ligne):
    """Les natures NON NULLES de la ligne, dans l'ordre MAT, MO, ST."""
    m = montants_par_nature(ligne)
    return [n for n in NATURES if m[n] != 0]


def vider_le_cache():
    """Pour les bancs. Un cache qui survit d'un cas à l'autre fait passer au
    vert un cas qui aurait dû appeler le hub."""
    _CACHE.clear()


def charger(jwt_brut, organization_id):
    """La correspondance de l'organisation, ou None. NE LÈVE JAMAIS.

    None veut dire « je ne sais pas », PAS « il n'y en a pas » — et dans les
    deux cas les natures tombent en « non associé ». C'est la seule
    dégradation acceptable : ne rien affirmer plutôt qu'affirmer faux.
    Le JWT de l'APPELANT est transmis tel quel : le hub répond pour SON
    organisation, et la garde 036 ci-dessous vérifie que c'est la bonne."""
    if not organization_id:
        return None
    cle = str(organization_id)
    entree = _CACHE.get(cle)
    if entree and (time.time() - entree[0]) < _TTL_SECONDS:
        return entree[1]

    if not jwt_brut:
        # Pas de jeton à présenter : on sert le cache même périmé, sinon rien.
        return entree[1] if entree else None

    try:
        req = urllib.request.Request(
            _url_hub() + _CHEMIN,
            headers={"Authorization": "Bearer " + jwt_brut,
                     "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SECONDS) as rep:
            ctype = (rep.headers.get("Content-Type") or "")
            brut = rep.read()
        # LE CONTRÔLE DE TYPE (trouvé par PC1 côté Ad CON) : un front SPA rend
        # 200 + HTML sur N'IMPORTE QUEL chemin. Sans lui, une HUB_API_URL qui
        # pointe le front donnerait une panne au mauvais nom.
        if "json" not in ctype.lower():
            logger.warning("[correspondance] reponse non JSON (%s) : "
                           "HUB_API_URL pointe-t-elle l'API ou le front ?", ctype)
            return entree[1] if entree else None
        charge = json.loads(brut.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — aucune panne du hub ne casse Ad BUD
        logger.warning("[correspondance] hub injoignable (%s : %s) ; %s",
                       type(e).__name__, e,
                       "cache perime servi" if entree else "aucun cache, tout en non associe")
        return entree[1] if entree else None

    # ⚠ LA GARDE 036 — CELLE QUI EMPÊCHE DE REFAIRE LA 023 D'AD CON. Celle-ci
    # a posé le plan comptable d'une entreprise sur 18 lignes de DEUX AUTRES.
    # Le hub répond selon le JWT ; si l'organisation qu'il déclare n'est PAS
    # celle du projet, on ne se sert de RIEN, et on ne le met PAS en cache.
    # Mieux vaut « non associé » que faux.
    # (Écart assumé avec l'original : une charge qui n'est pas un objet JSON
    # est rejetée ici au lieu de lever sur `.get` — « ne lève jamais ».)
    org_servie = str(charge.get("organization_id") or "") if isinstance(charge, dict) else ""
    if org_servie != cle:
        logger.warning("[correspondance] le hub a servi l'organisation %s pour un "
                       "projet de %s : charge REJETEE", org_servie, cle)
        return None

    _CACHE[cle] = (time.time(), charge)
    logger.info("[correspondance] org=%s : %s paires, %s regles (cache %ss)",
                cle, charge.get("n_paires"), charge.get("n_regles"), _TTL_SECONDS)
    return charge


def _morceaux(code, *, paire=False):
    """« 03 11 00.01 » -> ("03", "11", "00", "01") ; None si ce n'est pas un
    code CSI (au moins une division « DD »).

    Pour une PAIRE (`paire=True`), et seulement là, « DD DD 00 » se lit comme
    le GROUPE « DD DD » : la paire « 03 11 00 » couvre 03 11 13, 03 11 16…
    Mais « DD 00 00 » reste EXACT — objection de PC3 (8 oct.) : « 04 00 00 »
    posé pour les lignes codées 04 00 00 ne doit pas se mettre à couvrir TOUTE
    la division 04 en silence. Un repli de division s'écrit EXPLICITEMENT
    (« 02 », décision de Simon pour Démolition)."""
    t = (code or "").strip()
    base, _, suffixe = t.partition(".")
    parts = base.split()
    if not parts or len(parts) > 3 or not all(len(p) == 2 and p.isdigit() for p in parts):
        return None
    if suffixe:
        return tuple(parts) + (suffixe.strip(),)
    # NE PAS « SIMPLIFIER » CETTE CONDITION (relecture de PC3, 8 oct.). Le
    # 3e « 00 » tombe SEULEMENT si le 2e n'est pas « 00 » :
    #   « 03 11 00 » -> ("03", "11")       : la paire couvre son groupe ;
    #   « 03 00 00 » -> ("03", "00", "00") : reste EXACT, n'avale pas la division.
    if paire and len(parts) == 3 and parts[2] == "00" and parts[1] != "00":
        parts = parts[:2]
    return tuple(parts)


def compte_par_prefixe(csi, nature, paires):
    """Niveau (b) : la paire POSÉE dont le code est le PLUS LONG PRÉFIXE du code
    de la ligne. Le plus précis l'emporte (« 02 82 » avant « 02 »).

    Mesuré par PC4 sur le budget 290 (côté Ad CON) : 1 191 lignes sur 1 209
    portent un suffixe (« 03 11 00.01 ») ; cherchée à l'identique, la paire ne
    trouvait que 9 lignes matériaux sur 322. « Jamais deviné » tient : un
    préfixe est une paire posée par un humain."""
    ligne = _morceaux(csi)
    if not ligne:
        return None
    meilleur, longueur = None, -1
    suffixe_nature = "|" + nature
    for cle, compte in (paires or {}).items():
        if not cle.endswith(suffixe_nature):
            continue
        p = _morceaux(cle[: -len(suffixe_nature)], paire=True)
        if p and len(p) <= len(ligne) and ligne[: len(p)] == p and len(p) > longueur:
            meilleur, longueur = compte, len(p)
    return meilleur


def comptes_par_nature(ligne, correspondance, options_projet=None):
    """{nature: compte_ou_None} pour les natures NON NULLES de la ligne.

    N'inclut QUE les natures présentes : un compte pour une nature absente
    laisserait croire qu'un montant existe. `correspondance=None` (hub muet)
    n'empêche pas (a0) et (a) : ce sont des valeurs DE LA LIGNE, sans charte."""
    presentes = natures_de_la_ligne(ligne)
    if not presentes:
        return {}

    paires = (correspondance or {}).get("paires") or {}
    regles = (correspondance or {}).get("regles_nature") or {}
    # INTERRUPTEURS (Simon, 8 oct. 2026) : MO / ST au compte du MÉTIER au lieu
    # du compte générique. Par organisation (charge du hub) ; `options_projet`
    # les surcharge si un appelant en passe un jour (aucun dans Ad BUD).
    options = dict((correspondance or {}).get("options") or {})
    options.update({k: v for k, v in (options_projet or {}).items() if v is not None})
    csi = (ligne.get("section") or "").strip()
    sur_la_ligne = ligne.get("compte_metier_force") or None

    out = {}
    for nature in presentes:
        # (a0) LE COMPTE ENREGISTRÉ POUR CETTE NATURE — gagne sur tout.
        compte = ligne.get(COLONNE_NATURE[nature]) or None
        # (a) l'ancien compte unique : à la nature unique, sinon au MATÉRIAU.
        if not compte and sur_la_ligne:
            if len(presentes) == 1 or nature == "MAT":
                compte = sur_la_ligne
        # (b) la paire (code CSI, nature) — le plus long préfixe posé.
        if not compte and csi:
            compte = compte_par_prefixe(csi, nature, paires)
        # (b') MO / ST au compte du MÉTIER (= la paire MAT du code) si demandé.
        if (not compte and csi and nature in ("MO", "ST")
                and options.get(nature.lower() + "_vers_metier")):
            compte = compte_par_prefixe(csi, "MAT", paires)
        # (c) la règle de nature.
        if not compte:
            compte = regles.get(nature)
        # (d) rien : « non associé », et ça y reste.
        out[nature] = compte or None
    return out
