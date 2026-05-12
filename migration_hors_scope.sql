-- ════════════════════════════════════════════════════════════════════════════
-- Ad BUD - Filtre divisions hors scope (mai 2026)
--
-- Marque les budget_lignes dont la division CSI n'est pas dans la liste des
-- divisions autorisees Ad BUD :
--   01 = frais generaux / conditions generales
--   20-23 = mecanique / CVAC / plomberie
--   25 = controles automatises
--   26-28 = electrique / communications / securite
--   31 = excavation / pieux
--
-- Toute autre division (ex: 09 finitions, 04 maconnerie, 32 amenagement
-- exterieur) est conservee mais marquee hors_scope=TRUE pour affichage
-- separe dans le frontend Ad BUD ("section Hors scope" en queue).
--
-- Source de verite : modules/ad_budget_constants.py::AUTHORIZED_DIVISIONS.
-- Cette migration est aussi appliquee inline par api.py::_ensure_schema()
-- au demarrage de l'app — ce fichier est conserve pour doc/ops.
--
-- Idempotent : peut tourner 100 fois sans effet (IF NOT EXISTS + condition
-- WHERE hors_scope = FALSE sur le backfill).
-- ════════════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS hors_scope BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill : marque les lignes existantes dont la division est hors scope.
-- REPLACE(' ','') normalise '23 05 00' -> '230500' puis on prend les 2
-- premiers chiffres. Le filtre hors_scope=FALSE garantit l'idempotence
-- (re-execution ne re-traite que les lignes pas encore marquees).
UPDATE ad_budget.budget_lignes
SET hors_scope = TRUE
WHERE hors_scope = FALSE
  AND LEFT(REPLACE(section, ' ', ''), 2) NOT IN
      ('01', '20', '21', '22', '23', '25', '26', '27', '28', '31');

-- Index partiel : la requete typique est "donne-moi les lignes hors_scope
-- d'un projet" (affichage section dediee). Cas d'usage rare vs requetes
-- in-scope normales → index partiel reduit la taille de l'index.
CREATE INDEX IF NOT EXISTS idx_budget_lignes_hors_scope
  ON ad_budget.budget_lignes (projet_id)
  WHERE hors_scope = TRUE;
