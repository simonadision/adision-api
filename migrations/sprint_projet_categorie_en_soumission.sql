-- Ad BUD — Catégorie d'affichage "Projet en soumission" (2 sept 2026).
--
-- Simon, en direct : "Il faudrait ajouter un onglet Projet en soumission.
-- Quand on clic sur l'onglet on voit les projet qui y sont classés. Donc
-- sur la page de projet on y voit seulement les projets non classé."
--
-- Étend la catégorie d'affichage (sprint_projet_categorie_affichage.sql,
-- 31 août 2026) d'un troisième état, aux côtés de 'en_cours' et 'archive'.
-- Toujours une organisation PURE de la vue, distincte du `statut` métier ;
-- NULL reste le défaut à la naissance d'un projet.
--
-- Migration non destructive : élargit la contrainte CHECK existante sans
-- toucher aux lignes déjà classées 'en_cours'/'archive'. Tracké
-- ad_budget.schema_migrations (joué une fois).

ALTER TABLE ad_budget.projets
  DROP CONSTRAINT IF EXISTS projets_categorie_affichage_chk;

ALTER TABLE ad_budget.projets
  ADD CONSTRAINT projets_categorie_affichage_chk
  CHECK (categorie_affichage IN ('en_cours', 'archive', 'en_soumission'));
