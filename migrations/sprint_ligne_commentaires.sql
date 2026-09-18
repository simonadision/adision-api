-- COMMENTAIRES SUR UNE LIGNE DE BUDGET (18 septembre 2026, Simon : « option 1 :
-- ajouter un commentaire à la description de l'item… une icône C en rouge…
-- on peut ajouter des commentaires, modifier, supprimer, date et nom de
-- l'éditeur »).
--
-- Un fil par ligne, distinct de la colonne `note` : chaque entrée garde son
-- auteur et ses dates. Supprimer la ligne supprime son fil. `projet_id` est
-- recopié pour compter les commentaires de tout un tableau en une lecture.
-- Le nom de l'auteur est figé à l'écriture : un compte supprimé ou renommé
-- ne rend pas un commentaire anonyme.
CREATE TABLE IF NOT EXISTS ad_budget.ligne_commentaires (
    id          BIGSERIAL PRIMARY KEY,
    ligne_id    INTEGER NOT NULL REFERENCES ad_budget.budget_lignes(id) ON DELETE CASCADE,
    projet_id   INTEGER NOT NULL,
    auteur_id   INTEGER,
    auteur_nom  TEXT NOT NULL,
    corps       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ligne_commentaires_ligne
    ON ad_budget.ligne_commentaires (ligne_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ligne_commentaires_projet
    ON ad_budget.ligne_commentaires (projet_id);
