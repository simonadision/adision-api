-- Ad BUD — Réparation des lignes dont la main-d'œuvre était GRATUITE
-- (9 septembre 2026).
--
-- Simon, capture à l'appui : « actuellement j'edite des materiaux et je n'ai
-- pas de taux donc ma main d'oeuvre reste a 0$ === tres dangereux. »
--
-- Une ligne avec des HEURES et un taux à 0 coûte 0 $ de main-d'œuvre dans la
-- soumission, sans le moindre signe à l'écran. Relevé avant écriture :
-- 3 lignes, 3 projets distincts.
--
-- CAUSE (corrigée dans le même lot, côté code) : le résolveur de taux par
-- défaut lisait la division en prenant les 2 PREMIERS CARACTÈRES de la
-- section. Ça marche pour « 06 40 00 », pas pour « 6 11 00.01 » — écriture
-- réelle en production, sans zéro devant : on en tirait « 6 » (espace
-- comprise), qui ne correspond à aucune division mappée. Aucun taux, donc 0.
-- Deux autres trous fermés en même temps : la création testait `is None`
-- alors que l'écran envoie toujours `0`, et la MODIFICATION n'appliquait
-- aucun défaut.
--
-- CETTE MIGRATION ne touche QUE les lignes à réparer : actives, avec des
-- heures, et un taux nul ou absent. Une ligne sans heures n'a pas besoin de
-- taux ; une ligne dont le taux est saisi n'est jamais écrasée.
--
-- Le taux posé est celui de la division CSI de la ligne (même règle que le
-- code), avec repli sur CHARPENTIER_C — Charpentier-menuisier Compagnon,
-- le métier de repli décidé par Simon. La division est normalisée comme dans
-- `_division_csi` : chiffres de tête complétés à 2.

UPDATE ad_budget.budget_lignes AS l
   SET taux_horaire = COALESCE(
         (SELECT t.taux_col17
            FROM ad_budget.csi_division_default_metier m
            JOIN ad_budget.taux_horaires t ON t.id = m.taux_id
           WHERE t.actif = TRUE
             AND m.csi_division = lpad(
                   COALESCE(substring(btrim(l.section) FROM '^[0-9]{1,2}'), ''), 2, '0')
         ),
         (SELECT t.taux_col17
            FROM ad_budget.taux_horaires t
           WHERE t.code = 'CHARPENTIER_C' AND t.actif = TRUE)
       ),
       updated_at = NOW()
 WHERE l.actif
   AND COALESCE(l.heures, 0) > 0
   AND COALESCE(l.taux_horaire, 0) = 0;
