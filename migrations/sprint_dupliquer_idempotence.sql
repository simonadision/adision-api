-- Ad BUD — tracer la source d'une copie de projet, et la rendre idempotente.
--
-- Contexte (11 sept 2026). Cinq faux jumeaux de la même soumission dans
-- app_hub.projects (770, 781-784, tous copies de 771). L'endpoint
-- POST /budget/projets/{id}/dupliquer (2 sept 2026) ne gardait AUCUNE trace
-- de ce dont une copie est la copie : ni la garde d'idempotence ni un futur
-- affichage « copie de X » n'avaient sur quoi s'appuyer.
--
-- duplique_de_projet_id porte l'id du projet Ad BUD source. NULL = projet
-- créé autrement (nouveau projet, révision, import). ON DELETE SET NULL :
-- perdre la source ne doit jamais faire disparaître la copie.
--
-- Idempotente (IF NOT EXISTS + garde sur pg_constraint) : rejouable depuis
-- une base vide comme sur la production.

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS duplique_de_projet_id INTEGER;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'projets_duplique_de_projet_id_fkey'
  ) THEN
    ALTER TABLE ad_budget.projets
      ADD CONSTRAINT projets_duplique_de_projet_id_fkey
      FOREIGN KEY (duplique_de_projet_id)
      REFERENCES ad_budget.projets(id) ON DELETE SET NULL;
  END IF;
END $$;

-- La garde d'idempotence interroge (source, auteur, date de création).
CREATE INDEX IF NOT EXISTS idx_projets_duplique_de
  ON ad_budget.projets (duplique_de_projet_id, user_id, created_at DESC)
  WHERE duplique_de_projet_id IS NOT NULL;
