-- Ad BUD — DEVIS : bloc signature enrichi (titre du responsable + organisation).
--
-- Le bloc signature passe à une colonne unique empilée :
--   nom -> titre -> organisation -> courriel -> téléphone (sans libellés).
-- titre_responsable : libre éditable. organisation : pré-remplie depuis
-- l'entrepreneur (org du projet) mais éditable.
--
-- Tracké schema_migrations (joué une fois). ADD COLUMN IF NOT EXISTS = idempotent.

ALTER TABLE ad_budget.devis ADD COLUMN IF NOT EXISTS titre_responsable TEXT;
ALTER TABLE ad_budget.devis ADD COLUMN IF NOT EXISTS organisation      TEXT;
