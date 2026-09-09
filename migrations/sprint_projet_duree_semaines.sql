-- Ad BUD — Durée du chantier en semaines (9 septembre 2026).
--
-- Sert UNIQUEMENT à l'indicateur d'effectif affiché dans la bulle
-- MAIN-D'ŒUVRE du récap : total des heures ÷ 40 h/semaine = homme-semaines,
-- ÷ durée = nombre d'hommes sur le chantier par semaine. C'est une lecture
-- de planification — aucun montant du budget n'en dépend, d'où l'absence
-- volontaire de ce champ dans SNAPSHOT_AFFECTING_FIELDS (l'éditer ne salit
-- pas le budget émis et ne bumpe aucune révision).
--
-- Champ PROPRE à Ad BUD, pas une donnée d'identité : il ne rejoint donc pas
-- _HUB_OWNED_BUD_FIELDS et reste éditable depuis Ad BUD. Distinct des
-- « date début/fin travaux » qui vivent dans Ad HUB : celles-ci décrivent le
-- calendrier contractuel, celui-ci l'hypothèse d'exécution de l'estimateur.
--
-- Un projet NAÎT sans durée (NULL) — la bulle reste exactement comme avant
-- tant que personne ne saisit rien.
--
-- Migration non destructive : ADD COLUMN nullable, DEFAULT NULL. Tracké dans
-- ad_budget.schema_migrations (joué une fois). IF NOT EXISTS par sécurité.
--
-- ROLLBACK : ALTER TABLE ad_budget.projets DROP COLUMN IF EXISTS duree_semaines;

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS duree_semaines NUMERIC NULL DEFAULT NULL;

-- Garde-fou BD doublant la validation applicative : une durée est soit
-- absente, soit strictement positive. Zéro est refusé — il ferait une
-- division par zéro dans l'indicateur.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.constraint_column_usage
     WHERE table_schema='ad_budget' AND table_name='projets'
       AND constraint_name='projets_duree_semaines_chk'
  ) THEN
    ALTER TABLE ad_budget.projets
      ADD CONSTRAINT projets_duree_semaines_chk
      CHECK (duree_semaines IS NULL OR duree_semaines > 0);
  END IF;
END $$;
