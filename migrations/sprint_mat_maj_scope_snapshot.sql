-- ════════════════════════════════════════════════════════════════════
-- Onglet MAJ phase 2 (Ad MAT) — FONDATIONS : scope + snapshot-prix
-- ════════════════════════════════════════════════════════════════════
-- Sur ad_budget.budget_lignes, pour détecter une dérive de prix Ad MAT :
--
--   item_ad_mat_scope ('master'|'client') : lève l'AMBIGUÏTÉ d'id. Les tables
--     items (master) et items_client (overlay org) ont des SERIAL qui SE
--     CHEVAUCHENT (5011 ids communs en prod ; ex. id 3745 = « Documents fin
--     chantier » master 800$ ET « Budget peintures » client 65$). item_id_ad_mat
--     seul ne dit PAS de quelle table vient l'item → sans scope, un item client
--     serait résolu comme master = détection fausse. Non négociable.
--
--   source_mat_prix_snapshot / source_mat_snapshot_at : prix de RÉFÉRENCE figé à
--     la pioche. Divergence = prix MAT courant ≠ snapshot. On N'utilise PAS le flag
--     prix_unitaire_override (path-dependent, surchargé « snapshot vs édité main »
--     → inutilisable ; prouvé : 2 lignes MAT prod = 1 TRUE / 1 FALSE).
--
-- Additive + idempotente (ADD COLUMN IF NOT EXISTS). Appliquée au boot par
-- _bootstrap_db (schema_migrations, une seule fois). Réversibilité :
--   ALTER TABLE ad_budget.budget_lignes
--     DROP COLUMN item_ad_mat_scope, DROP COLUMN source_mat_prix_snapshot,
--     DROP COLUMN source_mat_snapshot_at;
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS item_ad_mat_scope        TEXT        DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS source_mat_prix_snapshot NUMERIC     DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS source_mat_snapshot_at   TIMESTAMPTZ DEFAULT NULL;

-- CHECK souple (NULL autorisé pour toute ligne non-MAT). Guard manuel (ADD
-- CONSTRAINT n'a pas de IF NOT EXISTS natif).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'ad_budget' AND table_name = 'budget_lignes'
       AND constraint_name = 'ck_budget_lignes_mat_scope'
  ) THEN
    ALTER TABLE ad_budget.budget_lignes
      ADD CONSTRAINT ck_budget_lignes_mat_scope
      CHECK (item_ad_mat_scope IS NULL OR item_ad_mat_scope IN ('master', 'client'));
  END IF;
END $$;

-- Backfill : les lignes MAT EXISTANTES sont 'master' (leur description = l'item
-- MASTER de même id ; vérifié sur les 2 lignes legacy prod L11049/L11059). Snapshot
-- laissé NULL (pas de prix de pioche connu → ces lignes ne seront pas « divergentes »
-- tant qu'elles ne sont pas re-piochées ; comportement voulu, pas de faux positif).
UPDATE ad_budget.budget_lignes
   SET item_ad_mat_scope = 'master'
 WHERE item_id_ad_mat IS NOT NULL AND item_ad_mat_scope IS NULL;
