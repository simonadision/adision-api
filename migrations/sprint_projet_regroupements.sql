-- Ad BUD — Sous-totaux NATIFS du projet (2 sept 2026).
--
-- Nouvelle méthode, décidée par Simon en direct après plusieurs itérations
-- infructueuses sur une approche liée aux divisions du GABARIT (PR #90-94,
-- fond vert / drapeau sur une division, jamais fiable une fois le projet
-- déjà créé) :
--
-- "Dans ad bud. je dois pouvoir créer des sous-total. Donc j'ai besoin d un
-- bouton sous total. je dois pouvoir placer mon sous total ou je veux dans
-- le tableau. Un fois le sous total ajouter, une modale me demande quelle
-- division y inclure. LE grand total du projet se calcul alors via ces
-- sous totaux, s'il y a lieu."
--
-- Un sous-total est désormais une entité INDÉPENDANTE du projet, jamais
-- liée à une division précise — même contrat que ad_budget.gabarits.
-- regroupements ({nom, divisions: [numéros], apres: position}), mais posé
-- sur LE PROJET lui-même : créable, positionnable et modifiable
-- directement dans un budget déjà existant, plus jamais tributaire d'un
-- aller-retour par le gabarit d'origine.
--
-- Migration non destructive : ADD COLUMN nullable-par-défaut ('[]'::jsonb).
-- Tracké ad_budget.schema_migrations (joué une fois). IF NOT EXISTS par
-- sécurité.

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS regroupements JSONB NOT NULL DEFAULT '[]'::jsonb;
