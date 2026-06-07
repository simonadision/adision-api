-- Sprint Partage gabarits MASTER — pattern push-master réversible (calque Ad RES).
--
-- Permet de désigner un gabarit MASTER (super_admin) et de le POUSSER (copie
-- descendante deep-copy, RÉVERSIBLE) vers une/des org(s) cible(s). Réplique le
-- pattern du carnet Ad RES (app_central.contact_push_log) mais dans ad_budget
-- (repos/BD séparés). PAS de lien vivant, PAS de consentement pair-à-pair, PAS
-- d'anonymizer (structure + libellés uniquement).
--
-- BD ad_budget (PAS app_central). Idempotente (ADD COLUMN/CREATE IF NOT EXISTS).
-- Aucune vue dépendante connue sur ad_budget.gabarits (CRUD direct).

-- 1) Colonnes de partage sur le gabarit.
ALTER TABLE ad_budget.gabarits
  ADD COLUMN IF NOT EXISTS est_master BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS source_master_gabarit_id INTEGER NULL,
  ADD COLUMN IF NOT EXISTS push_id UUID NULL;

COMMENT ON COLUMN ad_budget.gabarits.est_master IS
  'Gabarit désigné MASTER (partageable via push descendant). super_admin only.';
COMMENT ON COLUMN ad_budget.gabarits.source_master_gabarit_id IS
  'Copie poussée : id du gabarit master d''origine (autre org). NULL = gabarit natif.';
COMMENT ON COLUMN ad_budget.gabarits.push_id IS
  'Batch du push (gabarit_push_log.push_id) ayant inséré cette copie. NULL sinon.';

-- Lookup « ce master a-t-il déjà été poussé chez cette org ? » (re-push additif).
CREATE INDEX IF NOT EXISTS idx_gabarits_source_master
  ON ad_budget.gabarits (organization_id, source_master_gabarit_id)
  WHERE source_master_gabarit_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_gabarits_push_id
  ON ad_budget.gabarits (push_id) WHERE push_id IS NOT NULL;

-- 2) Audit + rollback des pushes (une ligne par (push → org cible)).
CREATE TABLE IF NOT EXISTS ad_budget.gabarit_push_log (
  push_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_source           UUID NOT NULL,
  org_cible            UUID NOT NULL,
  gabarit_master_id    INTEGER,
  pushed_by_user_id    TEXT,
  pushed_by_email      TEXT,
  count_inserted       INTEGER NOT NULL DEFAULT 0,
  count_replaced       INTEGER NOT NULL DEFAULT 0,
  count_skipped        INTEGER NOT NULL DEFAULT 0,
  snapshot_before      JSONB,
  snapshot_after       JSONB,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  rolled_back_at       TIMESTAMPTZ,
  rolled_back_by_email TEXT
);
CREATE INDEX IF NOT EXISTS idx_gabarit_push_log_cible
  ON ad_budget.gabarit_push_log (org_cible, created_at DESC);

DO $$
BEGIN
  RAISE NOTICE 'Migration partage gabarits MASTER : gabarits.est_master/source_master_gabarit_id/push_id + ad_budget.gabarit_push_log';
END $$;
