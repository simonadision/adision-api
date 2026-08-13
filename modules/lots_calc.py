"""Ad BUD — LOTS : calcul pur des sous-totaux par lot et du total projet.

Regroupement PHYSIQUE (lot_id, ex. "AB DEL", "CD DEL") — orthogonal au
regroupement CSI (section, classification par corps de metier). Une ligne
porte les deux, independamment.

MEME FORMULE que partout ailleurs dans Ad BUD : chaque ligne active
contribue _line_total() (mat+mo+st, ajustement inclus) — la fonction
"miroir exact" deja utilisee par budget_fingerprint.py et le front
(getRow() / budgetFingerprint.js). Aucune formule nouvelle n'est inventee
ici, pour ne jamais diverger du montant deja affiche a l'ecran.

Une ligne SANS lot_id (NULL) est "hors-lot" : elle n'entre dans aucun
sous-total de lot mais compte dans le total projet — compatibilite
garantie pour les projets existants sans lots (aucune regression).
Pur, sans acces DB : testable sans Postgres (cf. tests/test_lots_calc.py).
"""
from modules.budget_fingerprint import _line_total


def compute_lot_totals(lignes, lots, arrondi_dollar=False):
    """
    lignes : liste de dict bruts (memes champs que budget_lignes, incluant
             `lot_id` et `actif` -- et, pour le rappel "a completer",
             `id`, `description` et `a_completer`, tolerees absentes -->
             defaut None/False, cf. get()).
    lots   : liste de dict {id, nom, nb_logements, ordre} (meta des lots,
             sans total -- le total est toujours recalcule ici, jamais
             stocke, pour ne jamais diverger d'un prix modifie apres coup).
    arrondi_dollar : meme flag que projets.arrondi_dollar, pilote R() comme
             partout ailleurs (cf. budget_fingerprint._line_total).

    Retourne {
      "lots": [ {id, nom, nb_logements, ordre, nb_lignes, sous_total}, ... ],
               trie par ordre croissant (puis id a egalite d'ordre),
      "hors_lot": {"nb_lignes": int, "sous_total": float},
      "grand_total": float,   -- = somme de tous les lots + hors_lot
      "a_completer": [ {id, description, lot_id, lot_nom}, ... ],
               Phase C (workflow avance) -- lignes actives marquees
               a_completer=TRUE dont la quantite est ENCORE vide/0 au
               moment du calcul (une ligne completee sort automatiquement
               de cette liste, aucun etat a nettoyer). Reutilisee TELLE
               QUELLE par l'export (Excel + PDF, mode "par lot") pour le
               rappel avant soumission -- jamais recalculee ailleurs.
    }
    """
    arr = bool(arrondi_dollar)
    par_lot = {}
    hors_lot_total = 0.0
    hors_lot_nb = 0
    grand_total = 0.0
    lot_noms = {lot["id"]: lot["nom"] for lot in lots}
    a_completer = []

    for l in lignes:
        if not l.get("actif", True):
            continue
        val = _line_total(arr, l)
        grand_total += val
        lot_id = l.get("lot_id")
        if lot_id is None:
            hors_lot_total += val
            hors_lot_nb += 1
        else:
            entry = par_lot.setdefault(lot_id, {"sous_total": 0.0, "nb_lignes": 0})
            entry["sous_total"] += val
            entry["nb_lignes"] += 1
        if l.get("a_completer") and not float(l.get("qte") or 0):
            a_completer.append({
                "id": l.get("id"),
                "description": l.get("description") or "",
                "lot_id": lot_id,
                "lot_nom": lot_noms.get(lot_id, "Hors lot") if lot_id is not None else "Hors lot",
            })

    out_lots = []
    for lot in sorted(lots, key=lambda x: (x.get("ordre") or 0, x.get("id"))):
        agg = par_lot.get(lot["id"], {"sous_total": 0.0, "nb_lignes": 0})
        out_lots.append({
            "id": lot["id"],
            "nom": lot["nom"],
            "nb_logements": lot.get("nb_logements"),
            "ordre": lot.get("ordre") or 0,
            "nb_lignes": agg["nb_lignes"],
            "sous_total": agg["sous_total"],
        })

    return {
        "lots": out_lots,
        "hors_lot": {"nb_lignes": hors_lot_nb, "sous_total": hors_lot_total},
        "grand_total": grand_total,
        "a_completer": a_completer,
    }
