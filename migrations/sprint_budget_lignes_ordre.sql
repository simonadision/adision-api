-- Ad BUD — Sprint drag-items (17 août 2026).
--
-- ordre manuel des LIGNES de budget (glisser-déposer intra/cross-section),
-- calque exact du patron déjà en place pour ad_budget.lots.ordre (cf.
-- sprint_lots.sql). Sans cette colonne, budget_lignes n'a AUCUN ordre
-- stable : get_budget_lignes triait par `section, description`
-- (alphabétique pur) — impossible d'y superposer un ordre manuel, et donc
-- impossible de faire persister un glisser-déposer de ligne après un
-- rechargement.
--
-- Trackee schema_migrations (jouee une fois, cf. scripts/appliquer_
-- migrations_bud.py). IF NOT EXISTS par securite.

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS ordre INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_budget_lignes_ordre ON ad_budget.budget_lignes (projet_id, ordre);

-- Backfill : préserve l'ordre alphabétique ACTUEL (section, description) au
-- moment de la migration, gap=10 par section — pour que le PREMIER
-- chargement après déploiement ne bouge AUCUNE ligne à l'écran. Ne touche
-- QUE les lignes encore à ordre=0 : idempotent, ne réécrase jamais un ordre
-- déjà posé par un vrai glisser-déposer si la migration est rejouée.
WITH ranked AS (
  SELECT id, ROW_NUMBER() OVER (
    PARTITION BY projet_id, section ORDER BY description, id
  ) AS rn
  FROM ad_budget.budget_lignes
  WHERE ordre = 0
)
UPDATE ad_budget.budget_lignes bl
SET ordre = ranked.rn * 10
FROM ranked
WHERE bl.id = ranked.id;
