ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS item_id_ad_mat INTEGER,
  ADD COLUMN IF NOT EXISTS ad_hub_pending_id INTEGER;

COMMENT ON COLUMN ad_budget.budget_lignes.item_id_ad_mat IS
  'FK logique vers Ad MAT items.id (pas de FK physique cross-DB).';
COMMENT ON COLUMN ad_budget.budget_lignes.ad_hub_pending_id IS
  'FK logique vers Ad MAT pending_items.id (file Ad HUB).';

CREATE INDEX IF NOT EXISTS idx_bud_lignes_item_mat ON ad_budget.budget_lignes(item_id_ad_mat)
  WHERE item_id_ad_mat IS NOT NULL;
