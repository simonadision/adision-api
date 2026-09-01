-- Ad BUD — Catégorie d'affichage sur la page Projets (31 août 2026).
--
-- DEUX pastilles de tri, glisser-déposer : « Projet en cours » et
-- « Projet archivé ». C'est une organisation PURE de la vue, distincte
-- du `statut` métier (brouillon/adjuge/complet/perdu/archive) : un
-- projet complet peut très bien rester dans « Projet en cours » tant
-- que Simon ne l'a pas rangé lui-même.
--
-- Un projet NAÎT sans catégorie (NULL) — comportement par défaut
-- inchangé, aucune carte ne bouge tant que personne ne la glisse.
--
-- Migration non destructive : ADD COLUMN nullable, DEFAULT NULL. Tracké
-- ad_budget.schema_migrations (joué une fois). IF NOT EXISTS par sécurité.

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS categorie_affichage TEXT NULL DEFAULT NULL;

-- Garde-fou applicatif : seules ces deux valeurs, ou NULL (pas de catégorie).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.constraint_column_usage
     WHERE table_schema='ad_budget' AND table_name='projets'
       AND constraint_name='projets_categorie_affichage_chk'
  ) THEN
    ALTER TABLE ad_budget.projets
      ADD CONSTRAINT projets_categorie_affichage_chk
      CHECK (categorie_affichage IN ('en_cours','archive'));
  END IF;
END $$;
