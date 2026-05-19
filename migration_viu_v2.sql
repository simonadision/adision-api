-- ════════════════════════════════════════════════════════════════════════════
-- Ad BUD - Schema additif pour le push depuis Ad VIU v2.
-- A executer au demarrage de adision-api. Idempotent (IF NOT EXISTS).
-- ════════════════════════════════════════════════════════════════════════════

-- Tracabilite : lien entre une ligne de budget et l'item Ad VIU qui l'a cree.
-- Permet l'idempotence (re-push ne duplique pas) et le retour vers la source.
ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS source_viu_analysis_id INT NULL;

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS source_viu_item_id INT NULL;

-- Index pour le SELECT d'idempotence (WHERE projet_id = X AND source_viu_item_id = Y)
CREATE INDEX IF NOT EXISTS idx_budget_lignes_viu_item
  ON ad_budget.budget_lignes (source_viu_item_id)
  WHERE source_viu_item_id IS NOT NULL;
