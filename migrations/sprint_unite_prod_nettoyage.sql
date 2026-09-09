-- Ad BUD — Nettoyage des « Unité prod. » figées (9 septembre 2026).
--
-- SYMPTÔME : deux lignes voisines, même unité « pi2 », affichaient l'une
-- « pi2/h » et l'autre « global/h ».
--
-- CAUSE : App.jsx::saveLigneData repliait la SUGGESTION `${unite}/h` dans la
-- valeur ENREGISTRÉE. Toucher une ligne pour n'importe quelle raison — un
-- prix, une quantité — gravait donc l'unité du moment en base. Changer
-- l'unité ensuite ne changeait plus l'unité de production : la valeur gelée
-- l'emportait sur la suggestion à l'affichage. Corrigé côté client dans la
-- PR jumelle du monorepo (on ne persiste plus que la saisie explicite).
--
-- CE NETTOYAGE remet à NULL les valeurs qui ne sont QUE cette suggestion
-- figée, c'est-à-dire strictement égales à `unite || '/h'`. NULL veut dire
-- « suis l'unité » : l'affichage reconstruit exactement la même chaîne tant
-- que l'unité ne bouge pas, et la SUIT enfin quand elle bouge.
--
-- SANS PERTE, et c'est le point : on n'efface que des valeurs que l'affichage
-- reconstruit à l'identique. Les unités de production RÉELLEMENT
-- personnalisées (différentes de `unite || '/h'` — ex. quantité en pi2 mais
-- production comptée en feuilles/h) ne sont pas touchées. Relevé avant
-- écriture sur la base de production :
--     3 000 lignes  production_unite NULL      (déjà correctes)
--       942 lignes  = unite || '/h'            <- remises à NULL ici
--        45 lignes  réellement personnalisées  <- PRÉSERVÉES
--
-- Tracké dans ad_budget.schema_migrations (joué une fois).
--
-- ROLLBACK : aucun retour en arrière utile. Les valeurs retirées étaient
-- redondantes avec `unite` ; les réécrire ne ferait que recréer le gel.

UPDATE ad_budget.budget_lignes
   SET production_unite = NULL
 WHERE production_unite IS NOT NULL
   AND unite IS NOT NULL
   AND production_unite = unite || '/h';
