-- ════════════════════════════════════════════════════════════════════
-- Sprint Gabarits v4 — AUTEUR d'un gabarit (bibliothèque Ad BUD)
--
-- La bibliothèque affiche « qui a créé ce gabarit ». L'en-tête ne portait
-- que created_at / updated_at : aucune trace de l'auteur.
--
-- On DÉNORMALISE l'email, comme le fait déjà ad_budget.gabarit_push_log
-- (pushed_by_email / rolled_back_by_email) : cette API n'a PAS de table
-- `users` jointe — l'identité vient du jeton. Garder l'id ET l'email évite
-- une jointure cross-schéma qui n'existe pas ici.
--
-- Idempotente (ADD COLUMN IF NOT EXISTS). Les gabarits existants gardent
-- created_by = NULL → la bibliothèque affiche « — » : aucune perte, aucun
-- backfill inventé (on ne devine pas un auteur qui n'a jamais été écrit).
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.gabarits
  ADD COLUMN IF NOT EXISTS created_by       INTEGER;

ALTER TABLE ad_budget.gabarits
  ADD COLUMN IF NOT EXISTS created_by_email VARCHAR(255);
