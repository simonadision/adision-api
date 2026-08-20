"""HARNAIS DE FIDELITE du RAPPORT DE CALCUL â€” jsPDF (@adision/report-pdf) vs
reportlab servi (_build_projet_report).

Pour CHAQUE temoin (packages/report-pdf/harness/fixtures/R*.json) :
  - construit le master reportlab (moteur SERVI) via report_fidelity.reportlab_ref,
  - construit le candidat jsPDF via `node harness/render.mjs`,
  - compare sur 4 axes (texte multiset / ancrage layout / CELLULES / diff
    visuel flou), memes tolerances que le devis (8 pt / 3 %), NON negociables.

L'axe CELLULES (report_fidelity/corps_anchors.py) a ete ajoute parce que l'axe
layout n'ancre que 5 chaines fixes et ne voit AUCUNE cellule de table : un
decalage de cellule lui est invisible (cf. R01, 5,5 pt, vert a tort).

Chaque temoin porte ses OPTIONS de rendu + une date figee (today) -> master et
candidat portent la MEME Â« Date du jour Â».

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
from tests.report_fidelity.corps_anchors import axis_cellules  # noqa: E402
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
    ("footer", "PropulsÃ© par"),
    ("total_general", "TOTAL GÃ‰NÃ‰RAL"),
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
    print(f"\n  â”€â”€ {name} â”€â”€")
    dt = t["detail"]
    print(f"    [{mark(t['ok'])}] TEXTE   : {dt['n_cand']}/{dt['n_master']} mots"
          + ("" if t["ok"] else f" | manq={dt['manquantes'][:4]} trop={dt['en_trop'][:4]}"))
    worst = max((abs(r.get("dx", 0)) for r in l["detail"] if "dx" in r), default=0)
    worsty = max((abs(r.get("dy", 0)) for r in l["detail"] if "dy" in r), default=0)
    print(f"    [{mark(l['ok'])}] LAYOUT  : |Î”x|max={worst:.1f}pt |Î”y|max={worsty:.1f}pt (tol {C.TOL_LAYOUT_PT}pt)")
    for r in l["detail"]:
        if "dx" in r and not r.get("ok"):
            print(f"           Â· {r['ancre']}: Î”x={r['dx']} Î”y={r['dy']} (p{r['page_m']}->p{r['page_c']})")
        elif r.get("etat") == "ABSENTE":
            print(f"           Â· {r['ancre']}: ABSENTE (m={r['master']} c={r['cand']})")
    c = res.get("cellules")
    if c:
        cd = c["detail"]
        n = next((r.get("info") for r in cd if r["ancre"] == "cellules_appariees"), 0)
        pc_ = next((r for r in cd if r["ancre"] == "pire_cellule"), None)
        pire = max(abs(pc_["dx"]), abs(pc_["dy"])) if pc_ and "dx" in pc_ else 0
        print(f"    [{mark(c['ok'])}] CELLULES: {n} appariees | pire ecart={pire:.1f}pt"
              f" (tol {C.TOL_LAYOUT_PT}pt)")
        for r in cd:
            if r.get("etat"):
                print(f"           Â· {r['ancre']}: {r['etat']} "
                      f"(m={r['master']} c={r['cand']})")
            elif "dx" in r and not r.get("ok"):
                print(f"           Â· {r['ancre']}: Î”x={r['dx']} Î”y={r['dy']} {r.get('quoi','')}")
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
        try:
            master = render_reportlab(fx)
        except Exception as e:  # noqa: BLE001
            # Le moteur SERVI (reportlab) PLANTE sur cet input (ex. R13 : rangee
            # plus haute qu'une page -> LayoutError ; reportlab ne scinde pas une
            # rangee). Ce cas ne peut PAS etre juge en FIDELITE (la reference
            # s'ecroule) -> on le reclasse en ROBUSTESSE : le moteur CLIENT doit
            # rendre sans planter un PDF valide. Exclu du bilan de fidelite.
            emsg = str(e).splitlines()[0][:100]
            try:
                cand = _render_jspdf(fx_path)
                import fitz  # noqa: E402
                d = fitz.open(stream=cand, filetype="pdf")
                ok_robuste = cand[:5] == b"%PDF-" and d.page_count >= 1
                print(f"\n  â”€â”€ {name} â”€â”€ (ROBUSTESSE, hors fidelite)")
                print(f"    reportlab PLANTE : {type(e).__name__}: {emsg}")
                print(f"    [{'OK ' if ok_robuste else 'X  '}] jsPDF rend un PDF valide de {d.page_count} page(s) sans crash")
            except Exception as e2:  # noqa: BLE001
                ok_robuste = False
                print(f"\n  â”€â”€ {name} â”€â”€ (ROBUSTESSE)\n    [X  ] jsPDF a AUSSI echoue : {e2}")
            results.append((name, {"ok": ok_robuste, "robustness": True, "reportlab_crash": emsg}))
            continue
        cand = _render_jspdf(fx_path)
        res = C.compare(master, cand, base_anchors=REPORT_ANCHORS)
        # 4e axe : CELLULES. L'axe layout n'ancre que 5 chaines fixes et ne voit
        # AUCUNE cellule de table (cf. corps_anchors.py). Sans lui, un decalage
        # de cellule reste invisible et le lot est vert a tort.
        ok_c, det_c = axis_cellules(master, cand, C.TOL_LAYOUT_PT)
        res["cellules"] = {"ok": ok_c, "detail": det_c}
        res["ok"] = res["ok"] and ok_c
        if save_diffs:
            for i, d in enumerate(res.get("_diffs", [])):
                d.save(os.path.join(diff_dir, f"{name}_p{i}_diff.png"))
        _fmt_case(name, res)
        results.append((name, res))

    # Bilan FIDELITE = sur les temoins JUGEABLES (hors cas de robustesse ou la
    # reference reportlab s'ecroule). Robustesse rapportee a part.
    fidelite = [(n, r) for n, r in results if not r.get("robustness")]
    robust = [(n, r) for n, r in results if r.get("robustness")]
    n_ok = sum(1 for _, r in fidelite if r["ok"])
    print(f"\n{'='*64}")
    print(f"  BILAN FIDELITE RAPPORT : {n_ok}/{len(fidelite)} temoins jugeables verts sur 4 axes")
    if robust:
        rob_ok = sum(1 for _, r in robust if r["ok"])
        print(f"  ROBUSTESSE (reportlab plante) : {rob_ok}/{len(robust)} rendus jsPDF valides â€” {[n for n, _ in robust]}")
    if save_diffs:
        print(f"  Diffs visuels : {diff_dir}")
    print(f"{'='*64}")
    return n_ok, results


def _rouges_connus(bascule):
    """Temoins ROUGES tolerees, declares dans bascule.json avec leur raison.

    Ce n'est PAS un masquage : le temoin reste JOUE, son detail reste imprime,
    et s'il redevient VERT on le signale pour qu'on retire la derogation. Seul
    le VERDICT de blocage l'ignore. Tout autre rouge bloque normalement.
    """
    return dict(bascule.get("rouges_connus") or {})


def main():
    bascule, strict = _load_bascule()
    print(f"[bascule] moteur_defaut={bascule.get('default_engine')} strict={strict} "
          f"-> {'BLOQUANT' if strict else 'RAPPORT SEUL (coexistence)'}")
    n_ok, results = run_all()
    # Vert = tous les temoins JUGEABLES verts + tous les cas de robustesse OK.
    # (R01 portrait reste hors perimetre tant que le portrait n'est pas traite.)
    connus = _rouges_connus(bascule)
    rouges = [n for n, r in results if not r["ok"]]
    tolerees = [n for n in rouges if n in connus]
    bloquants = [n for n in rouges if n not in connus]
    for n in tolerees:
        print(f"  [TOLERE] {n} â€” rouge CONNU et documente, hors chemin critique :")
        print(f"           {connus[n]}")
    for n, r in results:
        if r["ok"] and n in connus:
            print(f"  [!] {n} est desormais VERT â€” retirer sa derogation de bascule.json.")
    all_green = bool(results) and not bloquants
    if strict:
        if not all_green:
            print(f"  [X] MODE STRICT : {bloquants} divergent -> pre-push BLOQUE.")
            return 1
        print("  [OK] MODE STRICT : aucun rouge non declare.")
        return 0
    return 0


def test_report_fidelity_gate():
    """Portillon pre-push. DOIT respecter la meme derogation "rouges_connus"
    que main() (bascule.json) -- sinon un temoin ROUGE TOLERE et documente
    (ex. R01-defaut-portrait, hors perimetre) bloque le push a tort, et
    l'equipe est poussee a contourner le portillon avec --no-verify au lieu
    de le corriger. C'est exactement l'incident du 18 aout 2026 : ce
    contournement a laisse un correctif financier (Coutant total) partir
    en prod sans etre relu. Le portillon reste BLOQUANT sur tout rouge
    NON documente -- seule la derogation explicite, nommee et justifiee
    dans bascule.json est toleree.
    """
    bascule, strict = _load_bascule()
    _n_ok, results = run_all(save_diffs=False)
    if not strict:
        return
    connus = _rouges_connus(bascule)
    rouges = [n for n, r in results if not r["ok"]]
    bloquants = [n for n in rouges if n not in connus]
    assert results and not bloquants, (
        "fidelite/robustesse rapport jsPDF<->reportlab hors tolerance (mode strict) "
        f"-- temoins non declares dans bascule.json[rouges_connus] : {bloquants}"
    )


if __name__ == "__main__":
    sys.exit(main())
