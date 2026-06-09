-- Ad BUD — Flag d'override du coût unitaire (MAT) saisi manuellement.
--
-- Cause racine prouvée : un re-snapshot Ad TYP AUTOMATIQUE (changement de qté ->
-- apply-typ) réécrivait prix_unitaire depuis l'assemblage Ad TYP (souvent 0),
-- écrasant une saisie manuelle. Aucun flag ne distinguait « coût saisi user »
-- de « coût hérité Ad TYP ».
--
-- prix_unitaire_override = TRUE dès que l'user édite le coût à la main. Les
-- resync AUTO (qté) préservent prix_unitaire si override=TRUE ; le re-tarif ⟳
-- EXPLICITE (acceptation) réaligne sur Ad TYP et remet le flag à FALSE.
--
-- Tracké ad_budget.schema_migrations (joué une fois). IF NOT EXISTS = idempotent.

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS prix_unitaire_override BOOLEAN NOT NULL DEFAULT FALSE;
