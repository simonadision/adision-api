-- Ad BUD — Mode de calcul Administration et profit (juin 2026).
--
-- Deux modes mutuellement exclusifs :
--   'global'  : admin+profit calculé par discipline en BLOC, ajouté au
--               sous-total avant taxes (comportement historique, défaut).
--   'ventile' : admin+profit VENTILÉ proportionnellement DANS chaque
--               item du budget. Le bloc global n'est ni calculé ni
--               affiché ni envoyé au PDF (anti-double-comptage strict).
--
-- Migration non destructive : tous les projets existants restent en
-- 'global' (DEFAULT). Bascule via PUT /budget/projets/{id} body
-- { pct_admin_mode: 'ventile' }.
--
-- Tracké ad_budget.schema_migrations (joué une fois). IF NOT EXISTS par
-- sécurité.

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS pct_admin_mode TEXT NOT NULL DEFAULT 'global';

-- Garde-fou applicatif : seuls 'global' et 'ventile' sont permis.
-- CHECK plutôt qu'ENUM (compat ALTER + plus simple à faire évoluer).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.constraint_column_usage
     WHERE table_schema='ad_budget' AND table_name='projets'
       AND constraint_name='projets_pct_admin_mode_chk'
  ) THEN
    ALTER TABLE ad_budget.projets
      ADD CONSTRAINT projets_pct_admin_mode_chk
      CHECK (pct_admin_mode IN ('global','ventile'));
  END IF;
END $$;
