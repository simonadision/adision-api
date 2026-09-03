-- sprint_projet_corbeille.sql
-- Corbeille Ad BUD (couche 1) — le DELETE /projets/{id} devient un
-- soft-delete. Décision Simon (brainstorm protection erreur utilisateur) :
-- supprimer un projet ne doit plus être un aller simple.
--
-- supprime_le / supprime_par : NULL = projet vivant, visible partout.
-- Non-NULL = dans la corbeille, exclu de toutes les listes, restaurable
-- via POST /admin/projets/{id}/restaurer.
--
-- Les budget_lignes ne sont PAS touchées : elles restent liées au projet,
-- invisibles seulement parce que le projet parent est filtré. Aucun DELETE
-- ne part plus vers ad_budget.projets, donc aucun risque de cascade.
--
-- Idempotent — le runner rejoue TOUS les *.sql à chaque boot.

ALTER TABLE ad_budget.projets
    ADD COLUMN IF NOT EXISTS supprime_le TIMESTAMPTZ NULL;

ALTER TABLE ad_budget.projets
    ADD COLUMN IF NOT EXISTS supprime_par TEXT NULL;

COMMENT ON COLUMN ad_budget.projets.supprime_le IS
    'Corbeille -- NULL = projet vivant. Non-NULL = soft-supprime, exclu des listes, restaurable.';
COMMENT ON COLUMN ad_budget.projets.supprime_par IS
    'Email/identifiant de l''utilisateur qui a supprime le projet.';

-- Index partiel : la corbeille (GET /admin/projets-supprimes) ne lit que les
-- lignes supprimees, et les listes normales filtrent supprime_le IS NULL sur
-- quasi tous les SELECT -- un index partiel sur les deux bornes reste utile
-- sans peser sur l'écriture des projets vivants.
CREATE INDEX IF NOT EXISTS idx_ad_budget_projets_supprime_le
    ON ad_budget.projets (supprime_le)
    WHERE supprime_le IS NOT NULL;
