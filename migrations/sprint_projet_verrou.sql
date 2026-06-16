-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — VERROUILLAGE de projet (lecture seule), INDÉPENDANT du statut
-- ════════════════════════════════════════════════════════════════════
-- Un projet VERROUILLÉ refuse TOUTE écriture (lignes + attributs + ⟳/MAJ) côté
-- backend (409), quel que soit son statut de cycle de vie (brouillon/adjuge/…).
-- Dimension SÉPARÉE : ne touche NI `statut`, NI ALLOWED_STATUTS, NI les
-- DEFINITIVE_STATUSES. N'importe quel user autorisé peut verrouiller/déverrouiller.
--
-- `verrouille_par` = email (traçabilité : qui a verrouillé), `verrouille_le` = quand.
-- Additif + idempotent (ADD COLUMN IF NOT EXISTS). DEFAULT FALSE → TOUS les projets
-- existants restent DÉVERROUILLÉS (comportement actuel strictement inchangé).
-- Appliqué au boot par _bootstrap_db (schema_migrations, une seule fois).
-- Réversibilité :
--   ALTER TABLE ad_budget.projets
--     DROP COLUMN is_verrouille, DROP COLUMN verrouille_par, DROP COLUMN verrouille_le;
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS is_verrouille  BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS verrouille_par TEXT        NULL,
  ADD COLUMN IF NOT EXISTS verrouille_le  TIMESTAMPTZ  NULL;
