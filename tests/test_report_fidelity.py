"""HARNAIS DE FIDELITE du RAPPORT DE CALCUL — jsPDF (@adision/report-pdf) vs
reportlab servi (_build_projet_report).

Pour CHAQUE temoin (packages/report-pdf/harness/fixtures/R*.json) :
  - construit le master reportlab (moteur SERVI) via report_fidelity.reportlab_ref,
  - construit le candidat jsPDF via `node harness/render.mjs`,
  - compare sur 3 axes (texte multiset / ancrage layout / diff visuel flou),
    memes tolerances que le devis (8 pt / 3 %), NON negociables.

Chaque temoin porte ses OPTIONS de rendu + une date figee (today) -> master et
candidat portent la MEME « Date du jour ».

DISCIPLINE DE BASCULE (tests/report_fidelity/bascule.json) :
  - default_engine='reportlab' + strict=false -> RAPPORT SEUL (exit 0). reportlab
    reste servi ; le harnais n'est pas bloquant tant que le lot n'est pas vert.
  - strict=true OU default_engine='jspdf'      -> BLOQUANT (jour de la bascule).

Lancer :  python tests/test_report_fidelity.py
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from tests.report_fidelity.reportlab_ref import render_reportlab  # noqa: E402
from tests.devis_fidelity import compare as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
API_ROOT = os.path.dirname(HERE)
RD_ROOT = os.path.dirname(API_ROOT)
REPORT_PKG = os.path.join(RD_ROOT, "adision-monorepo", "packages", "report-pdf")
FIXTURES = os.path.join(REPORT_PKG, "harness", "fixtures")
RENDER_JS = os.path.join(REPORT_PKG, "harness", "render.mjs")
BASCULE = os.path.join(HERE, "report_fidelity", "bascule.json")

# Ancres stables du rapport (presentes dans quasi tous les temoins).
REPORT_ANCHORS = [
    ("titre_rapport", "Rapport de budget"),
    ("bloc_client", "CLIENT"),
    ("bloc_entrepreneur", "ENTREPRENEUR"),
    ("footer", "Propulsé par"),
    ("total_general", "TOTAL GÉNÉRAL"),
]


def _load_bascule():
    try:
        with open(BASCULE, encoding="utf-8") as fh:
            b = json.load(fh)
    except Exception:  # noqa: BLE001
        b = {}
    strict = bool(b.get("strict")) or b.get("default_engine") == "jspdf"
    return b, strict


def _render_jspdf(fixture_path):
    out = tempfile.mktemp(suffix=".pdf")
    r = subprocess.run(["node", RENDER_JS, fixture_path, out], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"node render.mjs a echoue : {r.stderr.strip()}")
    with open(out, "rb") as fh:
        data = fh.read()
    try:
        os.remove(out)
    except OSError:
        pass
    return data


def _fmt_case(name, res):
    t, l, v = res["texte"], res["layout"], res["visuel"]
    mark = lambda ok: "OK " if ok else "X  "
    print(f"\n  ── {name} ──")
    dt = t["detail"]
    print(f"    [{mark(t['ok'])}] TEXTE   : {dt['n_cand']}/{dt['n_master']} mots"
          + ("" if t["ok"] else f" | manq={dt['manquantes'][:4]} trop={dt['en_trop'][:4]}"))
    worst = max((abs(r.get("dx", 0)) for r in l["detail"] if "dx" in r), default=0)
    worsty = max((abs(r.get("dy", 0)) for r in l["detail"] if "dy" in r), default=0)
    print(f"    [{mark(l['ok'])}] LAYOUT  : |Δx|max={worst:.1f}pt |Δy|max={worsty:.1f}pt (tol {C.TOL_LAYOUT_PT}pt)")
    for r in l["detail"]:
        if "dx" in r and not r.get("ok"):
            print(f"           · {r['ancre']}: Δx={r['dx']} Δy={r['dy']} (p{r['page_m']}->p{r['page_c']})")
        elif r.get("etat") == "ABSENTE":
            print(f"           · {r['ancre']}: ABSENTE (m={r['master']} c={r['cand']})")
    vd = v["detail"]
    fr = ", ".join(f"p{p['page']}={p['frac']*100:.1f}%" for p in vd["pages"])
    print(f"    [{mark(v['ok'])}] VISUEL  : {fr} (tol {C.TOL_VISUAL_FRAC*100:.0f}%) pages {vd['n_cand']}/{vd['n_master']}")


def run_all(save_diffs=True):
    if not os.path.isdir(FIXTURES):
        print(f"[SKIP] fixtures introuvables ({FIXTURES})")
        return 0, []
    diff_dir = os.path.join(tempfile.gettempdir(), "report-fidelity-diffs")
    if save_diffs:
        os.makedirs(diff_dir, exist_ok=True)

    results = []
    # NB : le FS Windows est insensible a la casse -> R*.json attrape real-*.json.
    # On filtre explicitement les temoins de RENDU (R## uniquement).
    paths = [p for p in glob.glob(os.path.join(FIXTURES, "R*.json"))
             if os.path.basename(p)[:1] == "R" and os.path.basename(p)[1:2].isdigit()]
    for fx_path in sorted(paths):
        name = os.path.basename(fx_path)[:-5]
        fx = json.load(open(fx_path, encoding="utf-8"))
        master = render_reportlab(fx)
        cand = _render_jspdf(fx_path)
        res = C.compare(master, cand, base_anchors=REPORT_ANCHORS)
        if save_diffs:
            for i, d in enumerate(res.get("_diffs", [])):
                d.save(os.path.join(diff_dir, f"{name}_p{i}_diff.png"))
        _fmt_case(name, res)
        results.append((name, res))

    n_ok = sum(1 for _, r in results if r["ok"])
    print(f"\n{'='*64}")
    print(f"  BILAN FIDELITE RAPPORT : {n_ok}/{len(results)} temoins verts sur 3 axes")
    if save_diffs:
        print(f"  Diffs visuels : {diff_dir}")
    print(f"{'='*64}")
    return n_ok, results


def main():
    bascule, strict = _load_bascule()
    print(f"[bascule] moteur_defaut={bascule.get('default_engine')} strict={strict} "
          f"-> {'BLOQUANT' if strict else 'RAPPORT SEUL (coexistence)'}")
    n_ok, results = run_all()
    all_green = results and n_ok == len(results)
    if strict:
        if not all_green:
            print("  [X] MODE STRICT : des temoins divergent -> pre-push BLOQUE.")
            return 1
        print("  [OK] MODE STRICT : lot temoin vert.")
        return 0
    return 0


def test_report_fidelity_gate():
    _bascule, strict = _load_bascule()
    n_ok, results = run_all(save_diffs=False)
    if strict:
        assert results and n_ok == len(results), "fidelite rapport jsPDF<->reportlab hors tolerance (mode strict)"


if __name__ == "__main__":
    sys.exit(main())
