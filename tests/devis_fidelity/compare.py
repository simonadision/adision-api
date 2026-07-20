"""Comparateur de fidelite jsPDF <-> reportlab sur TROIS AXES.

  1. TEXTE EXTRAIT     — memes lignes de texte (multiset), tolerance zero sur le
                         contenu (les chaines DOIVENT etre identiques).
  2. REPERES DE MISE   — position (x, y) d'ancres cles (barre montant, blocs,
     EN PAGE             titre, footer). |Δx|,|Δy| <= tolerance en points.
  3. DIFF VISUEL A     — rendu image par page, fraction de pixels differents
     SEUIL               au-dela d'un seuil de canal <= tolerance.

Tout via PyMuPDF (fitz) + Pillow. Aucune dependance externe supplementaire.
"""
import io
import re

import fitz
from PIL import Image, ImageChops, ImageFilter

# ── Tolerances (le point de bascule : jsPDF devient defaut quand tout <= seuil) ─
TOL_LAYOUT_PT = 8.0      # ecart de position max par ancre (points)
TOL_VISUAL_FRAC = 0.03   # fraction de pixels differents max par page (apres flou)
VISUAL_DPI = 100
VISUAL_CHANNEL = 60      # seuil de difference par canal (0-255), APRES flou
VISUAL_BLUR = 2.5        # flou gaussien px : absorbe l'anti-aliasing (bruit HF des
                         # bords de glyphes entre 2 rasteriseurs), garde les ecarts
                         # de bloc/texte. Balaye via le harnais : 03 aligne -> 1.4%,
                         # 01+logo -> 2.1%, ecart reel de titre -> 3-7%.


def _words(pdf_bytes):
    """Multiset des MOTS du document (toutes pages). Insensible au point de
    retour a la ligne (fait de mise en page, capte par l'axe visuel) et a
    l'ordre d'extraction : l'axe TEXTE controle le CONTENU, pas la colonne de
    coupe. Strict sur les chaines (memes mots, memes comptes)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    words = []
    for page in doc:
        txt = re.sub(r"\s+", " ", page.get_text("text"))
        words.extend(w for w in txt.split(" ") if w)
    return words


def axis_text(master_bytes, cand_bytes):
    """Compare les multisets de mots. Retourne (ok, details)."""
    from collections import Counter
    m = Counter(_words(master_bytes))
    c = Counter(_words(cand_bytes))
    manquants = list((m - c).elements())   # dans reportlab, absents de jsPDF
    en_trop = list((c - m).elements())     # dans jsPDF, absents de reportlab
    ok = not manquants and not en_trop
    return ok, {"manquantes": manquants, "en_trop": en_trop,
                "n_master": sum(m.values()), "n_cand": sum(c.values())}


def _first_pos(doc, needle):
    """Position (x0, y0) de la 1re occurrence d'une ancre, page + rect."""
    for pno in range(doc.page_count):
        rects = doc[pno].search_for(needle)
        if rects:
            r = rects[0]
            return (pno, r.x0, r.y0)
    return None


def axis_layout(master_bytes, cand_bytes, anchors):
    """Compare la position d'ancres. Retourne (ok, details par ancre)."""
    dm = fitz.open(stream=master_bytes, filetype="pdf")
    dc = fitz.open(stream=cand_bytes, filetype="pdf")
    rows = []
    ok = True
    for label, needle in anchors:
        pm = _first_pos(dm, needle)
        pc = _first_pos(dc, needle)
        if pm is None or pc is None:
            rows.append({"ancre": label, "etat": "ABSENTE",
                         "master": pm, "cand": pc})
            ok = False
            continue
        dx = round(pc[1] - pm[1], 1)
        dy = round(pc[2] - pm[2], 1)
        same_page = pm[0] == pc[0]
        within = same_page and abs(dx) <= TOL_LAYOUT_PT and abs(dy) <= TOL_LAYOUT_PT
        ok = ok and within
        rows.append({"ancre": label, "page_m": pm[0], "page_c": pc[0],
                     "dx": dx, "dy": dy, "ok": within})
    return ok, rows


def _page_image(doc, pno):
    pix = doc[pno].get_pixmap(dpi=VISUAL_DPI, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def axis_visual(master_bytes, cand_bytes):
    """Fraction de pixels differents par page. Retourne (ok, details, images_diff)."""
    dm = fitz.open(stream=master_bytes, filetype="pdf")
    dc = fitz.open(stream=cand_bytes, filetype="pdf")
    npages = min(dm.page_count, dc.page_count)
    pages = []
    ok = dm.page_count == dc.page_count
    diff_imgs = []
    for pno in range(npages):
        im = _page_image(dm, pno)
        ic = _page_image(dc, pno)
        if im.size != ic.size:
            ic = ic.resize(im.size)
        # Flou : absorbe le lisere d'anti-aliasing (bruit HF) avant le diff.
        if VISUAL_BLUR:
            im = im.filter(ImageFilter.GaussianBlur(VISUAL_BLUR))
            ic = ic.filter(ImageFilter.GaussianBlur(VISUAL_BLUR))
        diff = ImageChops.difference(im, ic)
        # Masque : pixel « different » si un canal depasse le seuil.
        gray = diff.convert("L").point(lambda p: 255 if p > VISUAL_CHANNEL else 0)
        hist = gray.histogram()
        diff_px = hist[255] if len(hist) > 255 else sum(hist[1:])
        total = im.size[0] * im.size[1]
        frac = diff_px / total if total else 0.0
        within = frac <= TOL_VISUAL_FRAC
        ok = ok and within
        pages.append({"page": pno, "frac": round(frac, 4), "ok": within})
        diff_imgs.append(diff)
    return ok, {"pages": pages, "n_master": dm.page_count, "n_cand": dc.page_count}, diff_imgs


# Ancres par defaut : elements stables presents dans (presque) tous les temoins.
DEFAULT_ANCHORS = [
    ("montant_libelle", "MONTANT DES TRAVAUX (avant taxes)"),
    ("bloc_entrepreneur", "ENTREPRENEUR"),
    ("bloc_client", "CLIENT"),
    ("documents", "DOCUMENTS CONSULTÉS"),
    ("footer", "Propulsé par"),
]


def compare(master_bytes, cand_bytes, montant_str=None, extra_anchors=None, base_anchors=None):
    # base_anchors : jeu d'ancres de base (defaut = DEFAULT_ANCHORS, specifiques
    # au devis). Le rapport passe SON propre jeu -> ne pas chercher les ancres
    # devis (« MONTANT DES TRAVAUX », « DOCUMENTS CONSULTÉS ») absentes du rapport.
    anchors = list(DEFAULT_ANCHORS if base_anchors is None else base_anchors)
    if montant_str:
        anchors.append(("montant_valeur", montant_str))
    if extra_anchors:
        anchors.extend(extra_anchors)
    ok_t, det_t = axis_text(master_bytes, cand_bytes)
    ok_l, det_l = axis_layout(master_bytes, cand_bytes, anchors)
    ok_v, det_v, diffs = axis_visual(master_bytes, cand_bytes)
    return {
        "texte": {"ok": ok_t, "detail": det_t},
        "layout": {"ok": ok_l, "detail": det_l},
        "visuel": {"ok": ok_v, "detail": det_v},
        "ok": ok_t and ok_l and ok_v,
        "_diffs": diffs,
    }
