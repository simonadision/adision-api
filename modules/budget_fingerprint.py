"""Empreinte SHA-256 de l'état du budget qu'un DEVIS représente (Option B).

Motif : le client peut construire un devis HORS LIGNE à partir d'un état local,
puis l'émettre plus tard une fois reconnecté. Entre les deux, le budget peut
avoir changé. L'empreinte, calculée À LA GÉNÉRATION (client) et RECALCULÉE À
L'ÉMISSION (serveur, état courant), permet de détecter la dérive — SANS bloquer
(on informe ; l'empreinte figée est conservée avec le PDF émis pour l'audit).

CE QUI ENTRE DANS L'EMPREINTE (et pourquoi — patron acquit Maestro : capturer
EXACTEMENT ce que le document reflète, ni plus, ni moins) :
  - montant_avant_taxes (en cents)  : la valeur que le devis AFFICHE.
  - revision_no + revision_label    : la révision à laquelle le devis appartient.
  - arrondi_dollar                  : pilote R() -> change le montant sans changer
                                      les lignes.
  - pct_admin_* (16 champs : 4 disciplines x base/mat/mo/st) : l'administration &
                                      profit qui détermine le montant depuis les
                                      lignes.
  - pct_admin_mode (global/ventile) : inclus car le brief le demande explicitement
                                      comme paramètre A&P ; NB il ne change PAS le
                                      montant total (compute_budget_totals l'ignore),
                                      c'est le seul champ « redondant » toléré.
  - condensé des lignes : par ligne CONTRIBUTIVE (id trié), la contribution
                          R(total) en cents — quantité calculée par le MÊME chemin
                          que le montant (aligné D2), donc concordante client/serveur.

EXCLU volontairement (ferait diverger sans que le document change) : description,
unite d'affichage, note, sous_traitant_nom, timestamps, colonnes override.

Arrondi : _js_round(n)=floor(n+0.5) = JS Math.round -> le client (Math.round) et
le serveur produisent le MÊME entier. Nombres en entiers (cents = x100, pct =
x10000) pour éliminer tout formatage de flottant dans la chaîne canonique.
"""
import hashlib

from modules.aggregates import _js_round
from modules.aggregates import heures_effectives as _heures_effectives

SCHEMA = "adision-devis-budget-fp/v1"
NULL = "null"  # marqueur ASCII (aucun risque d'encodage) pour un pct absent

# 4 disciplines x {discipline, mat, mo, st} = 16 champs, triés par nom.
PCT_ADMIN_FIELDS = sorted(
    f"pct_admin_{d}{s}"
    for d in ("conditions", "architecture", "mecanique", "excavation")
    for s in ("", "_mat", "_mo", "_st")
)


def _cents(x):
    return int(_js_round(float(x or 0) * 100))


def _i10000(x):
    return int(_js_round(float(x) * 10000))


def _line_total(arr, l):
    """R(total) d'UNE ligne, EXACTEMENT comme compute_budget_totals / getRow.

    Heures effectives : SOURCE UNIQUE modules.aggregates.heures_effectives (importée
    ci-dessus) — production_valeur > 0 gagne TOUJOURS (priorité absolue sur `heures`
    et `heures_manuelles`), cf. incident 2026-08-20 (voir docstring du module)."""
    qte = float(l.get("qte") or 0)
    heures = _heures_effectives(l.get("unite"), l.get("heures"), l.get("heures_manuelles"), qte,
                                l.get("production_valeur"))
    taux = float(l.get("taux_horaire") or 0)
    # Règle #2 (2026-08-17, décision Simon) : QTÉ=0 exclut TOUJOURS le montant
    # ST — même gate que compute_budget_totals (ad_budget_api.py) et getRow
    # (App.jsx). Sans lui, l'empreinte du devis (et lots_calc.py, qui délègue
    # à cette fonction) continuerait de compter une ligne que l'écran/le
    # rapport excluent désormais -> dérive détectée à tort à l'émission.
    st_montant = float(l.get("sous_traitant_montant") or 0) if qte > 0 else 0.0
    prix = float(l.get("prix_unitaire") or 0)
    ajm = float(l.get("ajust_materiaux") or 0)
    ajmo = float(l.get("ajust_main_oeuvre") or 0)
    ajst = float(l.get("ajust_sous_traitant") or 0)
    adj = float(l.get("ajustement_pct") or 0)
    st = qte * prix * (1 + ajm / 100) + heures * taux * (1 + ajmo / 100) + st_montant * (1 + ajst / 100)
    tot_real = st * (1 + adj / 100)
    return float(_js_round(tot_real)) if arr else tot_real


def _condense(projet, raw_lines):
    arr = bool(projet.get("arrondi_dollar"))
    out = []
    for l in raw_lines:
        c = _cents(_line_total(arr, l))
        if c != 0:  # ligne contributive (miroir du filtre R(tot)==0 -> skip)
            out.append((int(l.get("id")), c))
    out.sort(key=lambda t: t[0])
    return out


def _pct_admin_part(projet):
    parts = []
    for k in PCT_ADMIN_FIELDS:
        v = projet.get(k)
        parts.append(f"{k}={NULL}" if (v is None or v == "") else f"{k}={_i10000(v)}")
    return ";".join(parts)


def canonical_string(projet, raw_lines, montant):
    arr = bool(projet.get("arrondi_dollar"))
    cond = _condense(projet, raw_lines)
    header = [
        SCHEMA,
        f"montant_cents={_cents(montant)}",
        f"revision_no={int(projet.get('revision_no') or 0)}",
        f"revision_label={projet.get('revision_label') or 'Originale'}",
        f"arrondi={1 if arr else 0}",
        f"pct_admin_mode={projet.get('pct_admin_mode') or 'global'}",
        f"pct_admin={_pct_admin_part(projet)}",
        f"lignes={len(cond)}",
    ]
    lignes = [f"{i}={c}" for i, c in cond]
    return "\n".join(header + lignes)


def compute_budget_fingerprint(projet, raw_lines, montant):
    """SHA-256 hex de la chaîne canonique. `raw_lines` = lignes ACTIVES ;
    `montant` = montant_avant_taxes (client à la génération, ou serveur recalculé)."""
    s = canonical_string(projet, raw_lines, montant)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fingerprint_from_current_state(projet, active_lines):
    """Empreinte de l'état COURANT (serveur) : recalcule le montant via le chemin
    partagé D2 puis hache. Utilisé à l'émission pour comparer à l'empreinte reçue."""
    from modules.ad_budget_api import _prix_vente_total
    montant = _prix_vente_total(projet, active_lines)
    return compute_budget_fingerprint(projet, active_lines, montant)
