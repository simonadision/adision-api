-- Ad BUD — Gabarits d'EXPORT PDF (rapports quantitatifs).
--
-- Un gabarit d'export = un jeu réutilisable de paramètres du générateur PDF
-- existant (GET /budget/projets/{id}/pdf) : quelles COLONNES afficher + options
-- (sous-totaux, admin&profit, taxes, prix, orientation). Distinct des gabarits
-- de BUDGET (ad_budget.gabarits = structure de lignes).
--
-- Portée : PAR ORGANISATION (partagés entre users de l'org), scope strict
-- organization_id. UN seul gabarit par défaut par org (is_default).
--
-- Tracké par ad_budget.schema_migrations (api.py) → joué UNE fois. IF NOT EXISTS
-- par sécurité.

CREATE TABLE IF NOT EXISTS ad_budget.export_gabarits (
  id              SERIAL PRIMARY KEY,
  organization_id UUID NOT NULL,
  nom             VARCHAR(200) NOT NULL,
  -- Liste ORDONNÉE des clés de colonnes incluses (ex. ["section","description",
  -- "qte","unite","heures","sous_traitant_nom"]). Clés = colonnes du PDF.
  colonnes        JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Options du rapport : {avec_prix, sous_totaux:[...], admin_profits:[...],
  -- avec_sous_total_avant_taxes, avec_tps, avec_tvq, orientation, ...}.
  options         JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_default      BOOLEAN NOT NULL DEFAULT FALSE,
  created_by      INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_export_gabarits_org
  ON ad_budget.export_gabarits (organization_id);

-- Un seul défaut par organisation.
CREATE UNIQUE INDEX IF NOT EXISTS uq_export_gabarits_default
  ON ad_budget.export_gabarits (organization_id) WHERE is_default;
