-- FACTEUR D'UNITÉ (2 oct. 2026, Simon) : « clôture de chantier = 1139, unité
-- plin/mois… la fonction veut connaître le nombre de mois et calcule par
-- rapport à la quantité ». La quantité reste 1139 ; qte_facteur (6) multiplie
-- ce qu'elle paie : montant = 1139 × 6 × 5 $. Appliqué par la règle unique
-- quantite_effective (Python) / quantiteEffective (JS) — jamais aux heures.
-- NULL = pas de facteur : toutes les lignes existantes gardent leur montant.
ALTER TABLE ad_budget.budget_lignes ADD COLUMN IF NOT EXISTS qte_facteur NUMERIC NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'budget_lignes_qte_facteur_positif') THEN
    ALTER TABLE ad_budget.budget_lignes
      ADD CONSTRAINT budget_lignes_qte_facteur_positif CHECK (qte_facteur IS NULL OR qte_facteur > 0) NOT VALID;
  END IF;
END $$;
