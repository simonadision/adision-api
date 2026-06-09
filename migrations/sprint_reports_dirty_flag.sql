-- Ad BUD — Espace Rapports : flag « budget modifié depuis la dernière émission ».
--
-- Remplace le bump AUTOMATIQUE de révision (décision option A). Désormais une
-- mutation snapshot post-émission ne bumpe PLUS : elle marque seulement ce flag.
-- À l'émission, si la révision courante a déjà été émise ET que ce flag est
-- TRUE, le système POSE LA QUESTION à l'user (Nouvelle révision vs Correction).
-- Le bump devient une conséquence du choix « Nouvelle révision ». Le flag est
-- remis à FALSE à chaque émission.
--
-- Tracké ad_budget.schema_migrations (joué une fois). IF NOT EXISTS = idempotent.

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS budget_modifie_depuis_emission BOOLEAN NOT NULL DEFAULT FALSE;
