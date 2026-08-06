-- ════════════════════════════════════════════════════════════════════════
-- Ad BUD — de quel gabarit un budget est-il issu
--
-- CONTEXTE
--   Les titres personnalisés de section CSI (app_est.csi_titres, migration
--   067 côté Ad EST) se résolvent projet > gabarit > organisation. Pour
--   qu'un budget HÉRITE des titres de son gabarit, il faut savoir de quel
--   gabarit il vient. Ad EST le sait déjà (projects.source_template_id) ;
--   Ad BUD, non : POST /budget/projets/{id}/insert-gabarit insérait les
--   lignes sans jamais noter d'où elles venaient.
--
--   Sans cette colonne, la moitié « gabarit » du besoin n'existe pas dans
--   Ad BUD : ni héritage descendant, ni remontée vers le gabarit.
--
-- LE PREMIER GABARIT GAGNE
--   Écrit en COALESCE à l'insertion : un budget dans lequel on insère un
--   deuxième gabarit reste rattaché au premier. Même règle qu'Ad EST
--   (estimation_templates_api.apply_template : « COALESCE(source_template_id, %s) »),
--   et pour la même raison : l'origine d'un projet est un fait daté de sa
--   naissance, pas la dernière chose qu'on lui a versée dedans.
--
-- ON DELETE SET NULL
--   Supprimer un gabarit ne doit pas emporter les budgets qui en sont nés.
--   Le lien se coupe, le budget reste, et il retombe simplement sur les
--   titres de portée organisation.
--
-- IDEMPOTENCE : ADD COLUMN IF NOT EXISTS + FK posée sous garde.
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS source_gabarit_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'projets_source_gabarit_fk'
          AND table_schema = 'ad_budget'
    ) THEN
        ALTER TABLE ad_budget.projets
          ADD CONSTRAINT projets_source_gabarit_fk
          FOREIGN KEY (source_gabarit_id)
          REFERENCES ad_budget.gabarits(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS projets_source_gabarit_idx
  ON ad_budget.projets (source_gabarit_id)
  WHERE source_gabarit_id IS NOT NULL;
