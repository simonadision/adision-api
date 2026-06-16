-- Ad BUD — unité « hr » : heures = quantité par défaut (Option 2 : défaut + override).
--
-- heures_manuelles = flag d'OVERRIDE : TRUE quand l'utilisateur a saisi des heures à la
-- main → sa saisie prime, JAMAIS écrasée par le défaut « heures=qté » des lignes unité hr.
-- FALSE (défaut) → pour une ligne unité hr, les heures effectives = la quantité.
--
-- Backfill : on PRÉSERVE les lignes hr existantes qui ont DÉJÀ des heures<>0 (override),
-- pour ne PAS écraser leur MO avec le défaut qté (cas réels : projets 149/152/154, dont
-- des lignes qté=0 / heures<>0 où le défaut aurait détruit le MO). Les lignes hr à
-- heures=0 (le bug) restent FALSE → elles passent au défaut « heures=qté » (corrigées).

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS heures_manuelles BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE ad_budget.budget_lignes
   SET heures_manuelles = TRUE
 WHERE LOWER(TRIM(unite)) IN ('hr', 'hre', 'heure', 'heures', 'h')
   AND COALESCE(heures, 0) <> 0
   AND heures_manuelles = FALSE;
