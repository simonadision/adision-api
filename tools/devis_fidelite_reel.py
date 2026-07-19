"""Harnais de fidelite jsPDF<->reportlab sur DEVIS REELS (read-only).

Lance via (depuis adision-api) :
  railway run --service Postgres python tools/devis_fidelite_reel.py
adision-bud n'expose DATABASE_PUBLIC_URL que sur le service Postgres (pas web).
AUCUNE ecriture, aucune emission (read-only strict). Requiert le monorepo frere
(adision-monorepo) + node pour le rendu jsPDF.

Construit une fixture par projet ayant un devis (texte reel : presentation /
travaux / identite snapshot), rend les DEUX moteurs et compare sur 3 axes.
Detecte explicitement : accents/apostrophes typographiques, texte long,
montants inhabituels, logos disponibles.
"""
import os
import sys
import json
import subprocess
import tempfile
import re

# tools/ -> racine adision-api (robuste quel que soit le cwd).
API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, API_ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import psycopg
from psycopg.rows import dict_row
from tests.devis_fidelity.reportlab_ref import render_reportlab
from tests.devis_fidelity import compare as C

RD = os.path.dirname(API_ROOT)
DEVIS_PKG = os.path.join(RD, "adision-monorepo", "packages", "devis-pdf")
RENDER_JS = os.path.join(DEVIS_PKG, "harness", "render.mjs")

APOS_TYPO = "’"           # ' apostrophe typographique
COMPOSED = "œæ«»—…"  # œ æ « » — …
ACCENTS = "àâäéèêëîïôöùûüçÀÂÉÈÊËÎÏÔÖÛÜÇ"


def _fixture_from_row(r, montant=125000.0, logo_asset=None):
    snap = r.get("hub_identity_snapshot") or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except Exception:
            snap = {}
    docs = r.get("documents") or []
    if isinstance(docs, str):
        try:
            docs = json.loads(docs)
        except Exception:
            docs = []
    return {
        "case": f"proj-{r['id']}",
        "couleur": r.get("couleur") or "adision",
        "montant": montant,
        "dateDevis": "2026-07-19",
        "revisionLabel": r.get("revision_label") or "Originale",
        "nomProjet": (snap.get("nom") or ""),
        # entreprise = synthetique (l'info org/logo vient du hub, hors DB) — meme
        # valeur des 2 cotes => n'introduit aucune divergence.
        "entreprise": {"name": "Entreprise", "rbq": "0000-0000-00",
                       "courriel": "info@ex.ca", "telephone": "000 000-0000"},
        "client": {"nom": snap.get("nom_client") or "", "contact": snap.get("contact_client") or "",
                   "courriel": snap.get("email_client") or "", "telephone": snap.get("telephone_client") or ""},
        "presentation": r.get("presentation") or "",
        "travauxInclus": r.get("travaux_inclus") or "",
        "travauxNonInclus": r.get("travaux_non_inclus") or "",
        "documents": docs,
        "signature": {"nom": r.get("responsable_nom") or "", "titre": r.get("titre_responsable") or "",
                      "organisation": r.get("organisation") or "", "courriel": r.get("responsable_email") or "",
                      "telephone": r.get("responsable_tel") or ""},
        "logoAsset": logo_asset,
        "identitySource": "snapshot" if snap else "hub",
        "identitySnapshotAt": str(r.get("hub_identity_snapshot_at") or ""),
    }


def _render_jspdf(fixture):
    fx_path = tempfile.mktemp(suffix=".json")
    with open(fx_path, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, ensure_ascii=False)
    out = tempfile.mktemp(suffix=".pdf")
    res = subprocess.run(["node", RENDER_JS, fx_path, out], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"node: {res.stderr.strip()[:200]}")
    data = open(out, "rb").read()
    for p in (fx_path, out):
        try:
            os.remove(p)
        except OSError:
            pass
    return data


def _frca(v):
    f = f"{float(v or 0):,.2f}"
    return f.replace(",", " ").replace(".", ",")


def _txt_traits(fx):
    blob = " ".join([fx["presentation"], fx["travauxInclus"], fx["travauxNonInclus"],
                     fx["client"]["nom"], fx["client"]["contact"], fx["nomProjet"],
                     " ".join(fx["documents"])])
    return {
        "apos_typo": APOS_TYPO in blob,
        "accents": any(c in blob for c in ACCENTS),
        "composes": [c for c in COMPOSED if c in blob],
        "travaux_len": len(fx["travauxInclus"]) + len(fx["travauxNonInclus"]),
        "pres_len": len(fx["presentation"]),
    }


def main():
    url = os.environ.get("DATABASE_PUBLIC_URL")
    if not url:
        print("DATABASE_PUBLIC_URL absent"); return 2
    conn = psycopg.connect(url, connect_timeout=20, row_factory=dict_row)
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.organization_id, p.revision_label, p.hub_identity_snapshot,
               p.hub_identity_snapshot_at, p.arrondi_dollar,
               d.presentation, d.travaux_inclus, d.travaux_non_inclus, d.responsable_nom,
               d.titre_responsable, d.organisation, d.responsable_email, d.responsable_tel,
               d.documents, d.couleur
        FROM ad_budget.devis d JOIN ad_budget.projets p ON p.id = d.projet_id
        WHERE COALESCE(d.presentation,'')<>'' OR COALESCE(d.travaux_inclus,'')<>''
              OR COALESCE(d.travaux_non_inclus,'')<>''
        ORDER BY p.id DESC
    """)
    rows = cur.fetchall()
    nb_orgs = len({r.get("organization_id") for r in rows})
    cur.close(); conn.close()

    print(f"=== DEVIS REELS AVEC TEXTE : {len(rows)} (sur {nb_orgs} organisation(s)) ===")
    print("=== LOGOS : le devis utilise le logo de l'ORG (hub, httpx R2) — PAS dans cette")
    print("    base ; l'axe logo/rognage canvas ne peut PAS etre teste depuis la DB seule. ===\n")

    results = []
    cov = {"apos_typo": 0, "accents": 0, "composes": 0, "long": 0, "multipage": 0}
    for r in rows:
        fx = _fixture_from_row(r)
        traits = _txt_traits(fx)
        try:
            master = render_reportlab(fx, None)
            cand = _render_jspdf(fx)
            res = C.compare(master, cand, montant_str=_frca(fx["montant"]))
            import fitz
            pages = fitz.open(stream=cand, filetype="pdf").page_count
        except Exception as e:
            print(f"  proj-{r['id']:<6} ERREUR : {type(e).__name__}: {str(e)[:120]}")
            results.append((r["id"], None, traits)); continue
        ok = res["ok"]
        if traits["apos_typo"]:
            cov["apos_typo"] += 1
        if traits["accents"]:
            cov["accents"] += 1
        if traits["composes"]:
            cov["composes"] += 1
        if traits["travaux_len"] > 300 or traits["pres_len"] > 600:
            cov["long"] += 1
        if pages >= 3:
            cov["multipage"] += 1
        mark = "OK " if ok else "X  "
        flags = "".join([
            "’" if traits["apos_typo"] else " ",
            "é" if traits["accents"] else " ",
            "L" if (traits["travaux_len"] > 300 or traits["pres_len"] > 600) else " ",
            "3" if pages >= 3 else " ",
        ])
        line = f"  [{mark}] proj-{r['id']:<6} [{flags}] pages={pages}"
        if not ok:
            axes = [k for k in ("texte", "layout", "visuel") if not res[k]["ok"]]
            line += f"  ECHEC={axes}"
            if not res["texte"]["ok"]:
                dt = res["texte"]["detail"]
                line += f" | manquants={dt['manquantes'][:2]} en_trop={dt['en_trop'][:2]}"
            if not res["layout"]["ok"]:
                w = max((abs(x.get("dy", 0)) for x in res["layout"]["detail"] if "dy" in x), default=0)
                line += f" | Δy_max={w:.1f}pt"
            if not res["visuel"]["ok"]:
                vp = max((p["frac"] for p in res["visuel"]["detail"]["pages"]), default=0)
                line += f" | visuel_max={vp*100:.1f}%"
        print(line)
        results.append((r["id"], res, traits))

    oks = sum(1 for _, res, _ in results if res and res["ok"])
    n = sum(1 for _, res, _ in results if res is not None)
    print(f"\n=== BILAN REEL : {oks}/{n} devis verts sur 3 axes ===")
    print(f"Couverture axes : apostrophe_typo(’)={cov['apos_typo']}  accents={cov['accents']}  "
          f"composes(œ«»—…)={cov['composes']}  texte_long={cov['long']}  multipage(3+)={cov['multipage']}")

    # ── Montants inhabituels (sur le 1er projet reel) ────────────────────────
    if rows:
        print("\n=== MONTANTS INHABITUELS (proj de reference) ===")
        base = rows[0]
        for label, m in [("tres grand", 12345678.90), ("zero", 0.0), ("negatif", -4500.0)]:
            fx = _fixture_from_row(base, montant=m)
            try:
                master = render_reportlab(fx, None); cand = _render_jspdf(fx)
                res = C.compare(master, cand, montant_str=_frca(m))
                print(f"  [{'OK ' if res['ok'] else 'X  '}] {label:12} ({_frca(m)} $) "
                      f"texte={'ok' if res['texte']['ok'] else 'X'} "
                      f"layout={'ok' if res['layout']['ok'] else 'X'} "
                      f"visuel={'ok' if res['visuel']['ok'] else 'X'}")
            except Exception as e:
                print(f"  [X  ] {label}: {type(e).__name__}: {str(e)[:100]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
