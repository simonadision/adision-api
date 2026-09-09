-- Ad BUD — QUATRE statuts de projet, un seul vocabulaire (9 septembre 2026).
--
-- Simon : « Les statuts sont maintenant : Projet en soumission, projet en
-- cours et projet archivé. Partout. par defaut. » Puis, devant la perte de la
-- distinction gagné/perdu qu'imposaient trois valeurs : « Ajouter un onglet
-- projet perdu. »
--
-- Le quatrième n'est pas un ornement. Sans lui, un projet PERDU deviendrait
-- « archivé » au même titre qu'un projet simplement rangé, et le taux de
-- réussite d'Ad ANA (ViewByClient, ADJUDICATION_OK) n'aurait plus rien à lire.
--
-- CORRESPONDANCE (les trois premières sont les règles explicites de Simon,
-- les autres en découlent) :
--     brouillon        -> en_soumission
--     adjuge           -> en_cours        « Adjugé et complet = projets en cours »
--     complet          -> en_cours        idem
--     perdu            -> perdu           « perdu = projets archivé » -> son propre onglet
--     archive          -> archive
--
-- `statut` et `categorie_affichage` parlent désormais la MÊME langue. Ils ne
-- sont pas fusionnés — l'un est le statut métier, l'autre le rangement visuel
-- glissable de la page Projets — mais leurs valeurs coïncident enfin.
--
-- Relevé avant écriture : 19 projets en `brouillon`, 1 en `archive`.
-- Aucun `adjuge`, `complet` ni `perdu` en base à ce jour — la correspondance
-- est écrite quand même, pour les projets créés avant ce jour dans d'autres
-- environnements et pour rendre la règle lisible.

-- 1. Le CHECK d'abord : sans ça l'UPDATE serait refusé par l'ancienne
--    contrainte, qui ne connaît ni « en_soumission » ni « perdu ».
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projets_categorie_affichage_chk;

-- 2. Les statuts.
UPDATE ad_budget.projets SET statut = 'en_soumission' WHERE statut = 'brouillon';
UPDATE ad_budget.projets SET statut = 'en_cours'      WHERE statut IN ('adjuge', 'complet');
-- 'perdu' et 'archive' gardent leur valeur : déjà dans le nouveau vocabulaire.

-- 3. Le rangement visuel accueille le quatrième onglet.
ALTER TABLE ad_budget.projets
  ADD CONSTRAINT projets_categorie_affichage_chk
  CHECK (categorie_affichage IN ('en_soumission', 'en_cours', 'perdu', 'archive'));

-- 4. LA VALEUR PAR DÉFAUT DE LA COLONNE, avant tout CHECK. Elle valait
--    'brouillon' : un projet créé sans statut explicite serait REFUSÉ par la
--    contrainte posée juste après, avec une erreur incompréhensible à
--    l'écran. C'est le genre de détail qui ne se voit qu'en production, à la
--    première création.
ALTER TABLE ad_budget.projets ALTER COLUMN statut SET DEFAULT 'en_soumission';

-- 5. Garde-fou sur le statut lui-même. Il n'en avait AUCUN jusqu'ici : la
--    validation vivait uniquement côté application (ALLOWED_STATUTS). Une
--    écriture directe en base pouvait donc y poser n'importe quoi.
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projets_statut_chk;
ALTER TABLE ad_budget.projets
  ADD CONSTRAINT projets_statut_chk
  CHECK (statut IN ('en_soumission', 'en_cours', 'perdu', 'archive'));
