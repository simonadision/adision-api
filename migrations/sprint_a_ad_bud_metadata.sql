BEGIN;

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS type_batiment       VARCHAR(50),
  ADD COLUMN IF NOT EXISTS region              VARCHAR(100),
  ADD COLUMN IF NOT EXISTS date_adjudication   DATE,
  ADD COLUMN IF NOT EXISTS superficie_m2       NUMERIC(12, 2);

-- Index pour Ad ANA analytics futures (filtres par type / région / période)
CREATE INDEX IF NOT EXISTS idx_projets_type_batiment   ON ad_budget.projets(type_batiment);
CREATE INDEX IF NOT EXISTS idx_projets_region          ON ad_budget.projets(region);
CREATE INDEX IF NOT EXISTS idx_projets_date_adjudication ON ad_budget.projets(date_adjudication);

COMMIT;
