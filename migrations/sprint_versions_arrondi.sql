-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — UNE VERSION RETIENT L'ARRONDI AU DOLLAR QU'ELLE A VU
-- ════════════════════════════════════════════════════════════════════
-- 7 oct. 2026 (PC1, relu par PC3). `facteurs->base` est le total de chaque
-- ligne CALCULÉ AVEC l'arrondi au dollar du projet au moment de la version.
-- Si l'option `arrondi_dollar` bascule ensuite, chaque ligne à total non
-- entier change de 0,01 $ à 0,50 $ sans que personne n'y ait touché, et la
-- version affichait « N lignes modifiées » sur TOUTES ses lignes. Avec
-- l'arrondi stocké, `derive_version` compare la base au total recalculé sous
-- CET arrondi, et nomme la bascule à côté (`arrondi_change`) au lieu de la
-- compter en modifications.
--
-- NULL = inconnu : la dérive se calcule alors comme avant, sans rien deviner.

ALTER TABLE ad_budget.versions_debalancees
  ADD COLUMN IF NOT EXISTS arrondi_dollar BOOLEAN;

-- ⚠ REPRISE DES VERSIONS EXISTANTES : C'EST UNE SUPPOSITION, PAS UNE MESURE.
-- On pose sur chaque version l'arrondi ACTUEL de son projet. Ce n'est juste
-- que si l'option n'a pas changé depuis la création de la version, et rien
-- ne permet de le vérifier : `arrondi_dollar` n'a aucun historique avant le
-- journal des paramètres financiers du 7 oct. 2026 (même famille que les
-- pct_admin_*, enquête 7,887 pour cent du projet 290 ; aucun signe pour cent
-- dans ce fichier : psycopg le lit comme un paramètre, même en commentaire).
-- Portée mesurée le 7 oct. 2026 : UNE seule version en production, la V.1 de
-- « Rénovation intérieure d'unités de logements » (projet 290, arrondi_dollar
-- = true, 1 209 facteurs, toutes les bases entières — compatible avec
-- l'arrondi à true). Pour une version, la supposition est acceptable ; elle
-- n'est écrite ici que pour ne pas passer pour une reprise fidèle.
UPDATE ad_budget.versions_debalancees v
   SET arrondi_dollar = p.arrondi_dollar
  FROM ad_budget.projets p
 WHERE p.id = v.projet_id
   AND v.arrondi_dollar IS NULL;
