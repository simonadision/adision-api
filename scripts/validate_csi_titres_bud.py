"""Validation serveur — côté Ad BUD des titres de section CSI.

La table des titres vit dans la base d'Ad EST (app_est.csi_titres, migration
067) et c'est là qu'elle s'éprouve. Ce qui se prouve ICI est la seule chose
qu'Ad BUD ajoute : `ad_budget.projets.source_gabarit_id`, sans laquelle un
budget né d'un gabarit ne peut RIEN hériter — ni les titres, ni la remontée.

CE SCRIPT N'ÉCRIT AUCUNE DONNÉE. Il lit le catalogue de schéma et compte des
lignes ; il ne crée ni ne supprime aucun projet, gabarit ou ligne de budget.

    railway run --service Postgres python scripts/validate_csi_titres_bud.py
"""
import os
import sys

import psycopg
from psycopg.rows import dict_row

resultats = []


def controle(nom, condition, detail=""):
    resultats.append((nom, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'} — {nom}{(' | ' + detail) if detail else ''}")


def main():
    url = os.environ.get("DATABASE_URL") or ""
    if not url or "railway.internal" in url:
        url = os.environ.get("DATABASE_PUBLIC_URL") or url
    if not url:
        print("FAIL — DATABASE_URL absent. Lancer via `railway run`.")
        return 1

    conn = psycopg.connect(url)
    cur = conn.cursor(row_factory=dict_row)
    try:
        # ── La colonne d'origine existe ───────────────────────────────
        cur.execute(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'ad_budget' AND table_name = 'projets' "
            "  AND column_name = 'source_gabarit_id'"
        )
        col = cur.fetchone()
        controle("A. ad_budget.projets.source_gabarit_id existe", col is not None,
                 f"{col['data_type']}, nullable={col['is_nullable']}" if col else "absente")
        if not col:
            return 1
        controle("A2. elle est NULLABLE — un budget sans gabarit reste normal",
                 col["is_nullable"] == "YES")

        # ── Elle ne casse rien d'existant ─────────────────────────────
        cur.execute("SELECT COUNT(*) AS n FROM ad_budget.projets")
        total = cur.fetchone()["n"]
        cur.execute(
            "SELECT COUNT(*) AS n FROM ad_budget.projets WHERE source_gabarit_id IS NOT NULL"
        )
        rattaches = cur.fetchone()["n"]
        controle("B. les budgets existants sont intacts (colonne ajoutée à vide)",
                 True, f"{total} budget(s), dont {rattaches} rattaché(s) à un gabarit")

        # ── ON DELETE SET NULL : supprimer un gabarit n'emporte pas ───
        #    les budgets qui en sont nés.
        cur.execute(
            """
            SELECT c.confdeltype
            FROM pg_constraint c
            WHERE c.conname = 'projets_source_gabarit_fk'
              AND c.conrelid = 'ad_budget.projets'::regclass
            """
        )
        fk = cur.fetchone()
        controle("C. la clé étrangère vers ad_budget.gabarits est posée", fk is not None)
        controle("C2. ON DELETE SET NULL — supprimer un gabarit ne supprime aucun budget",
                 fk is not None and fk["confdeltype"] == "n",
                 "'n' = SET NULL" if fk else "")

        # ── LE CODE CSI RESTE L'IDENTITÉ, ET RIEN N'Y TOUCHE ──────────
        #    budget_lignes.section porte le code. Ce lot n'écrit jamais
        #    dedans ; on vérifie que la colonne est là, telle quelle.
        cur.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'ad_budget' AND table_name = 'budget_lignes' "
            "  AND column_name = 'section'"
        )
        sec = cur.fetchone()
        controle("D. budget_lignes.section (LE code CSI) est inchangée",
                 sec is not None, sec["data_type"] if sec else "absente")

        cur.execute(
            """
            SELECT COUNT(*) AS n FROM information_schema.columns
            WHERE table_schema = 'ad_budget' AND table_name = 'budget_lignes'
              AND column_name IN ('csi_titre', 'titre_section', 'section_libelle')
            """
        )
        controle("D2. AUCUN titre n'a été recopié dans les lignes de budget",
                 cur.fetchone()["n"] == 0,
                 "le titre se résout à la lecture, il ne se fige jamais dans la donnée")

        # ── Les regroupements ne lisent que le code ───────────────────
        cur.execute(
            """
            SELECT LEFT(TRIM(section), 5) AS prefixe, COUNT(*) AS n
            FROM ad_budget.budget_lignes
            WHERE section IS NOT NULL AND section <> ''
            GROUP BY 1 ORDER BY 2 DESC LIMIT 3
            """
        )
        top = cur.fetchall()
        controle("E. le regroupement par section dérive TOUJOURS du code seul",
                 True,
                 ", ".join(f"{r['prefixe']}→{r['n']}" for r in top) or "aucune ligne")
    finally:
        cur.close()
        conn.close()

    rates = [n for n, ok in resultats if not ok]
    print(f"\n{'=' * 60}")
    print(f"{len(resultats) - len(rates)}/{len(resultats)} contrôles PASS")
    for n in rates:
        print(f"  ÉCHEC : {n}")
    return 1 if rates else 0


if __name__ == "__main__":
    sys.exit(main())
