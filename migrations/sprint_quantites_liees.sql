-- QUANTITÉS LIÉES ENTRE LOTS (18 septembre 2026, Simon : « j'ai choisi
-- l'option de copier l'item aussi dans le lot Rimini avec les quantités ;
-- l'item est bien copié mais sans les quantités » -- il les tape dans le
-- tableau APRÈS la création, pas dans la fenêtre d'ajout).
--
-- Une copie créée « avec les quantités » pointe vers sa ligne d'origine et en
-- SUIT la quantité, les heures et la production -- jusqu'à ce qu'on la
-- retouche elle-même : elle devient alors indépendante (colonne remise à
-- NULL). Supprimer l'origine détache ses copies, sans les supprimer.
ALTER TABLE ad_budget.budget_lignes
    ADD COLUMN IF NOT EXISTS quantites_liees_a INTEGER
        REFERENCES ad_budget.budget_lignes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_budget_lignes_quantites_liees_a
    ON ad_budget.budget_lignes (quantites_liees_a)
    WHERE quantites_liees_a IS NOT NULL;
