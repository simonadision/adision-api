-- ════════════════════════════════════════════════════════════════════
-- Pont Ad TYP → Ad BUD (étape 1) : lien + snapshot sur budget_lignes
-- ════════════════════════════════════════════════════════════════════
-- Une ligne de budget peut être PIOCHÉE depuis le catalogue Ad TYP (adision_dig,
-- base distincte). On garde un lien LOGIQUE (pas de FK cross-base) vers le code
-- Ad TYP d'origine + l'horodatage du snapshot, pour permettre une re-tarif
-- (re-snapshot) UNIQUEMENT si le projet est au statut 'brouillon' (« En cours »).
-- Une ligne sans source_typ_code = ligne manuelle classique (inchangée).
--
-- Appliqué AUSSI au démarrage (idempotent) par api._ensure_schema(). Ce fichier
-- est la trace versionnée. Réversibilité :
--   ALTER TABLE ad_budget.budget_lignes
--     DROP COLUMN source_typ_code, DROP COLUMN source_typ_snapshot_at;
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS source_typ_code        TEXT        DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS source_typ_snapshot_at TIMESTAMPTZ DEFAULT NULL;
