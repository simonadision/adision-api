-- QUANTITÉ « AU MILLE » : STRICTEMENT SUR DEMANDE (1er oct. 2026).
--
-- La colonne qte_auto avait été créée DEFAULT TRUE : toutes les lignes
-- EXISTANTES passaient donc pour « calculées », et le calcul Cautionnement /
-- Assurance a ÉCRASÉ des quantités saisies à la main — budget 290
-- « Rénovation intérieure d'unités de logements », neuf lignes, 346 680 $ au
-- lieu de 26 850 $ (remises par PC3 depuis le dump de 03 h 00).
--
-- Règle, retenue par PC3 et PC4 : une colonne qui autorise une machine à
-- écrire par-dessus l'humain ne se crée JAMAIS avec un défaut permissif.
-- Désormais FALSE par défaut, et TOUTES les lignes existantes à FALSE : le
-- calcul ne vaut que pour une ligne où quelqu'un clique « calculer
-- automatiquement ».
--
-- Fichier joué UNE SEULE FOIS (ad_budget.schema_migrations) : un redémarrage
-- ne remet pas à FALSE une ligne mise en calcul automatique plus tard.

ALTER TABLE ad_budget.budget_lignes ALTER COLUMN qte_auto SET DEFAULT FALSE;
UPDATE ad_budget.budget_lignes SET qte_auto = FALSE WHERE qte_auto;
