-- Ad BUD — Espace Rapports : champs de RÉVISION portés par le budget/projet.
--
-- La révision est la source de vérité côté Ad BUD ; le HUB la recopie sur
-- chaque version de rapport (sans la posséder).
--   revision_no                  0 = « Originale », 1 = R.1, etc. (ordering + bump)
--   revision_label               affichage éditable (défaut calculé app-side)
--   emitted_at_current_revision  TRUE dès la 1re émission de cette révision ;
--                                toute mutation post-émission bump revision_no++
--                                et le remet à FALSE.
--
-- Rétrocompat : budgets existants -> revision_no=0, revision_label='Originale',
-- emitted_at_current_revision=FALSE (DEFAULT + backfill du label NULL).
--
-- Tracké ad_budget.schema_migrations (joué une fois). IF NOT EXISTS par sécurité.

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS revision_no INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS revision_label TEXT;
ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS emitted_at_current_revision BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill du label par défaut pour les budgets existants (révision 0).
UPDATE ad_budget.projets
   SET revision_label = 'Originale'
 WHERE revision_label IS NULL;
