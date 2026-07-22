-- sprint_projet_detenteur_email.sql
-- MIROIR de la clé de détention. FICHIER NEUF — voir l'explication complète
-- dans 112_projects_detenteur_email.sql côté hub : ajouter l'ALTER à la fin
-- d'une migration déjà appliquée ne la rejoue pas, et la colonne n'existe
-- jamais (500 UndefinedColumn observé en production le 2026-07-22).
--
-- C'est CETTE colonne que lit le gate d'écriture, et non detenteur_id.

ALTER TABLE ad_budget.projets
    ADD COLUMN IF NOT EXISTS detenteur_email TEXT NULL;

COMMENT ON COLUMN ad_budget.projets.detenteur_email IS
    'MIROIR de la CLE de detention. Compare a user[email] par _load_and_authorize_projet.';
