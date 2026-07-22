-- sprint_projet_detention.sql
-- MIROIR LOCAL de la détention (étape 1 du modèle « verrou + miroir »).
--
-- La SOURCE UNIQUE est app_hub.projects (migration 111 côté adision-app-api),
-- exactement comme pour le verrou de la migration 110. Ad BUD en tient un
-- miroir pour que le gate d'écriture (_load_and_authorize_projet) lise UNE
-- COLONNE LOCALE, sans appel cross-service à chaque écriture — c'est déjà le
-- schéma éprouvé du verrou, et il évite d'ajouter un aller-retour réseau sur
-- les 17 gates.
--
-- FRAÎCHEUR DU MIROIR : toutes les mutations de détention (prendre, rendre,
-- forcer, battement) passent par le MÊME endpoint Ad BUD, qui proxifie vers le
-- hub PUIS écrit le miroir dans la foulée. Le miroir n'est donc jamais en retard
-- sur une action venue d'Ad BUD. Il peut l'être sur une action venue d'un AUTRE
-- module — hors périmètre de l'étape 1, qui ne concerne qu'Ad BUD, et à
-- reprendre à l'étape 5 (montée dans le package commun).
--
-- ⚠ `detenteur_par_email` N'EXISTE PAS ICI, volontairement. Le verrou a hérité
-- d'une divergence de type (INTEGER côté hub, TEXT/email côté miroir) qui le
-- rend inexploitable pour afficher une présence. On ne la reproduit pas : le
-- miroir porte le MÊME type que la source.
--
-- Idempotent — le runner rejoue TOUS les *.sql à chaque boot.

ALTER TABLE ad_budget.projets
    ADD COLUMN IF NOT EXISTS detenteur_id INTEGER NULL;

ALTER TABLE ad_budget.projets
    ADD COLUMN IF NOT EXISTS detenteur_nom TEXT NULL;

ALTER TABLE ad_budget.projets
    ADD COLUMN IF NOT EXISTS detenu_depuis TIMESTAMPTZ NULL;

ALTER TABLE ad_budget.projets
    ADD COLUMN IF NOT EXISTS derniere_activite TIMESTAMPTZ NULL;

COMMENT ON COLUMN ad_budget.projets.detenteur_id IS
    'MIROIR de app_hub.projects.detenteur_id. Lu par le gate d''ecriture. NULL = libre.';
COMMENT ON COLUMN ad_budget.projets.detenteur_nom IS
    'MIROIR du nom affichable, denormalise pour rester lisible hors ligne.';
COMMENT ON COLUMN ad_budget.projets.detenu_depuis IS
    'MIROIR du debut de detention.';
COMMENT ON COLUMN ad_budget.projets.derniere_activite IS
    'MIROIR du dernier battement. Sert au calcul d''expiration (15 min) a la lecture.';
