-- Ad BUD — nouveau projet naît dans "Projet en soumission" (3 sept 2026).
--
-- Simon, en direct : "Visible Par defaut un nouveau projet sera ajouter
-- dans projet en soumission et quand on ouvre la page projet, par defaut
-- on sera dans la page de projets en soumission."
--
-- Jusqu'ici (sprint_projet_categorie_en_soumission.sql, 2 sept 2026) un
-- projet naissait SANS catégorie (NULL) -- décision volontaire à
-- l'époque, revue ici sur demande explicite de Simon. Change uniquement
-- le DÉFAUT DE COLONNE : aucun code Python ne pose categorie_affichage
-- à la création (vérifié : les 3 sites INSERT INTO ad_budget.projets qui
-- créent un projet omettent tous cette colonne), donc ce DEFAULT
-- s'applique automatiquement, sans toucher à modules/ad_budget_api.py.
--
-- Migration non destructive : ne touche à AUCUNE ligne existante (un
-- projet déjà NULL/en_cours/archive garde sa valeur -- seul le défaut
-- pour une FUTURE insertion change). Tracké ad_budget.schema_migrations
-- (joué une fois), même convention que les migrations sprint_* voisines.

ALTER TABLE ad_budget.projets
  ALTER COLUMN categorie_affichage SET DEFAULT 'en_soumission';
