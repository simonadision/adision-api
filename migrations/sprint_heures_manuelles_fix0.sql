-- Ad BUD — correction faux override à 0 (unité hr).
-- Un « override » heures_manuelles=TRUE avec heures=0 est un FAUX override (ex. apply-typ
-- d'un assemblage sans rendement, type Mobilisation/Démobilisation) → MO=0 au lieu du
-- défaut qté. On le remet à FALSE : la ligne retombe sur le défaut heures=qté.
-- NE touche PAS les lignes hr à heures<>0 (override légitime préservé, projets 152/154).

UPDATE ad_budget.budget_lignes
   SET heures_manuelles = FALSE
 WHERE LOWER(TRIM(unite)) IN ('hr', 'hre', 'heure', 'heures', 'h')
   AND COALESCE(heures, 0) = 0
   AND heures_manuelles = TRUE;
