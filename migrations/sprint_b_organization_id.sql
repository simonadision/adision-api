-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — organization_id sur ad_budget.projets (socle multi-tenant)
-- Rattache chaque projet / budget à une organisation cliente.
--
-- PAS de contrainte FK : app_central.organizations vit dans une autre base
-- PostgreSQL (backend adision-app-api) — clé étrangère cross-base impossible.
-- Intégrité garantie APPLICATIVEMENT (injection JWT à la création + fonction
-- d'autorisation centralisée, cf. PHASE 3).
--
-- Colonne NULLABLE à ce stade — passage NOT NULL différé après backfill.
-- Idempotent (ADD COLUMN / CREATE INDEX IF NOT EXISTS).
--
-- APPLICATION : jouée automatiquement au démarrage par api.py::_bootstrap_db()
-- (suivi via ad_budget.schema_migrations — exécutée une seule fois). PAS de
-- BEGIN/COMMIT explicite : _bootstrap_db encapsule chaque migration dans sa
-- propre transaction.
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS organization_id UUID;

CREATE INDEX IF NOT EXISTS idx_projets_organization
  ON ad_budget.projets (organization_id);
