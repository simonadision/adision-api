"""Axe CELLULES du harnais de fidelite du RAPPORT.

POURQUOI CET AXE EXISTE
-----------------------
L'axe LAYOUT historique n'ancre que 5 CHAINES FIXES (titre du rapport, blocs
CLIENT / ENTREPRENEUR, footer, TOTAL GENERAL) : **aucune cellule de table**. Un
decalage de cellule lui etait donc structurellement invisible.

Cas reel (R01, 2026-07-21) : la colonne « Sous-traitants et fournisseurs » etait
alignee a DROITE cote jsPDF alors que reportlab l'aligne a GAUCHE (cellule rendue
en Paragraph) -> 5,5 pt d'ecart. L'axe LAYOUT est reste VERT. L'ecart n'a ete
attrape que DE BIAIS par l'axe TEXTE, et par accident : chez le master la cellule
voisine demarrait pile au bord du mot, donc l'extracteur PyMuPDF fusionnait les
deux en « Pagui17 » et le multiset de mots divergeait. Sans cette collision
fortuite, l'ecart passait inapercu et le lot etait vert A TORT.

CE QUE FAIT CET AXE
-------------------
Il apparie TOUS les spans de TOUTES les pages PAR CONTENU (texte normalise +
rang d'occurrence dans la page, en ordre de lecture) et compare leur position.
Un decalage de cellule declenche alors le harnais DIRECTEMENT, pas de biais.

Sont rapportes nommement :
  - la PREMIERE et la DERNIERE cellule de chaque page (composition des pages),
  - les bandeaux/codes CSI (frontieres de section),
  - le PIRE ecart sur l'ensemble des cellules appariees.

Meme tolerance que l'axe layout (8 pt), NON negociable.
"""
import re

import fitz

# Code CSI : sert de bandeau de section ET de repere de rangee.
CODE_CSI = re.compile(r"^\d\d \d\d \d\d(\.\d+)?$")

# Plafond d'affichage des divergences d'occurrences (evite des centaines de
# lignes quand une page entiere se decale).
MAX_ROWS_OCC = 6


def _spans_par_page(doc):
    """MOTS par page, en ORDRE DE LECTURE, avec leur coin haut-gauche.

    ATTENTION — on extrait des MOTS (`get_text("words")`), PAS des spans. Le
    decoupage en spans depend de la facon dont chaque moteur groupe ses
    operateurs de texte dans le PDF, et il DIFFERE a rendu identique :
    reportlab emet « 0,00 Budget » en UN span la ou jsPDF en emet deux
    (« 0,00 » + « Budget »), et decoupe « AdFLO » en « A »/« d »/« FLO ».
    Apparier des spans produisait donc des divergences FANTOMES (« NOMBRE
    DIFFERENT ») sans le moindre ecart geometrique. Les mots sont decoupes sur
    les espaces : le decoupage est le MEME des deux cotes, et l'axe TEXTE
    garantit deja que le multiset de mots concorde.
    """
    pages = []
    for p in doc:
        out = [(w[4], w[0], w[1]) for w in p.get_text("words") if w[4].strip()]
        out.sort(key=lambda v: (v[2], v[1]))
        pages.append(out)
    return pages


# Tolerance de regroupement des mots en LIGNES. Les rangees font >= 18 pt et
# l'interligne intra-cellule >= 10 pt : 3 pt suffisent a regrouper une ligne
# sans jamais fusionner deux lignes voisines.
TOL_LIGNE_PT = 3.0


def _lignes_par_page(doc):
    """Mots regroupes en LIGNES (par y), chaque ligne triee par x.

    Apparier les mots par simple RANG dans la page ne marche pas : le residu
    vertical n'est PAS uniforme (0,0 pt sur certains mots, 1,0 a 1,7 sur
    d'autres), donc deux mots de meme y nominal peuvent basculer d'un cote a
    l'autre dans l'ordre de lecture. Resultat : le « : » du bloc CLIENT se
    retrouvait apparie au « : » de la sous-colonne ENTREPRENEUR -> 560 pt
    d'ecart FANTOME. En regroupant d'abord par ligne, la structure est stable
    des deux cotes et l'appariement devient deterministe.
    """
    pages = []
    for mots in _spans_par_page(doc):
        lignes = []
        for t, x, y in mots:
            if lignes and abs(y - lignes[-1][0]) <= TOL_LIGNE_PT:
                lignes[-1][1].append((t, x, y))
            else:
                lignes.append((y, [(t, x, y)]))
        pages.append([sorted(g, key=lambda v: v[1]) for _, g in lignes])
    return pages


def _apparier(occ_m, occ_c):
    """Apparie deux listes d'occurrences d'un MEME mot, au PLUS PROCHE VOISIN.

    Ni le rang dans la page ni le regroupement en lignes ne tiennent : le residu
    vertical n'est pas uniforme (0,0 a 1,7 pt selon les elements), donc deux mots
    de meme y nominal peuvent s'inverser. Deux faux positifs constates :
      - le « : » du bloc CLIENT apparie a celui de la sous-colonne ENTREPRENEUR
        (560 pt d'ecart FANTOME) ;
      - en portrait 20 colonnes, les titres replies en fragments empiles se
        regroupaient differemment en lignes (R01, alors que le VISUEL est a
        0,0 %).
    Le plus proche voisin est insensible a ces deux effets. Contrepartie assumee :
    un deplacement SUPERIEUR a la moitie de l'ecart entre deux occurrences du
    MEME mot serait sous-estime — les axes VISUEL (3 %) et TEXTE couvrent ce cas.
    """
    paires = sorted(
        ((abs(xb - xa) + abs(yb - ya), im, ic)
         for im, (xa, ya) in enumerate(occ_m)
         for ic, (xb, yb) in enumerate(occ_c)),
        key=lambda v: v[0],
    )
    pris_m, pris_c, out = set(), set(), []
    for _, im, ic in paires:
        if im in pris_m or ic in pris_c:
            continue
        pris_m.add(im)
        pris_c.add(ic)
        xa, ya = occ_m[im]
        xb, yb = occ_c[ic]
        out.append((round(xb - xa, 2), round(yb - ya, 2)))
    return out


def axis_cellules(master_bytes, cand_bytes, tol):
    """Compare la position de TOUTES les cellules. Retourne (ok, lignes de detail)."""
    dm = fitz.open(stream=master_bytes, filetype="pdf")
    dc = fitz.open(stream=cand_bytes, filetype="pdf")
    pm, pc = _spans_par_page(dm), _spans_par_page(dc)

    rows = []
    ok = True
    n_occ_rows = 0
    n_appariees = 0
    pire = {"ancre": "pire_cellule", "dx": 0.0, "dy": 0.0, "ok": True, "quoi": "(aucune)"}
    pire_csi = {"ancre": "pire_bandeau_csi", "dx": 0.0, "dy": 0.0, "ok": True, "quoi": "(aucun)"}

    if len(pm) != len(pc):
        rows.append({"ancre": "nb_pages", "etat": "DIFFERENT",
                     "master": len(pm), "cand": len(pc)})
        ok = False

    for i in range(min(len(pm), len(pc))):
        a, b = pm[i], pc[i]
        if not a or not b:
            continue

        # 1. Premiere / derniere cellule de la page : composition des pages.
        for etiq, idx in (("premiere", 0), ("derniere", -1)):
            ta, xa, ya = a[idx]
            tb, xb, yb = b[idx]
            if ta != tb:
                rows.append({"ancre": f"p{i}_{etiq}", "etat": "CONTENU DIFFERENT",
                             "master": ta[:32], "cand": tb[:32]})
                ok = False
                continue
            dx, dy = round(xb - xa, 2), round(yb - ya, 2)
            bon = abs(dx) <= tol and abs(dy) <= tol
            ok = ok and bon
            rows.append({"ancre": f"p{i}_{etiq}", "dx": dx, "dy": dy,
                         "ok": bon, "quoi": ta[:30]})

        # 2. Appariement par TEXTE, puis au PLUS PROCHE VOISIN dans le groupe.
        ga, gb = {}, {}
        for t, x, y in a:
            ga.setdefault(t, []).append((x, y))
        for t, x, y in b:
            gb.setdefault(t, []).append((x, y))

        for t, occ_m in ga.items():
            occ_c = gb.get(t, [])
            if len(occ_c) != len(occ_m):
                if n_occ_rows < MAX_ROWS_OCC:
                    rows.append({"ancre": f"p{i}_occurrences", "etat": "NOMBRE DIFFERENT",
                                 "master": f"{t[:26]} x{len(occ_m)}",
                                 "cand": f"x{len(occ_c)}"})
                    n_occ_rows += 1
                ok = False
                continue
            for dx, dy in _apparier(occ_m, occ_c):
                n_appariees += 1
                pire_ici = max(abs(dx), abs(dy))
                if pire_ici > max(abs(pire["dx"]), abs(pire["dy"])):
                    pire = {"ancre": "pire_cellule", "dx": dx, "dy": dy,
                            "ok": pire_ici <= tol, "quoi": f"p{i} « {t[:26]} »"}
                if CODE_CSI.match(t) and pire_ici > max(abs(pire_csi["dx"]), abs(pire_csi["dy"])):
                    pire_csi = {"ancre": "pire_bandeau_csi", "dx": dx, "dy": dy,
                                "ok": pire_ici <= tol, "quoi": f"p{i} « {t} »"}

    ok = ok and pire["ok"] and pire_csi["ok"]
    rows.append(pire)
    rows.append(pire_csi)
    rows.append({"ancre": "cellules_appariees", "info": n_appariees})
    return ok, rows
