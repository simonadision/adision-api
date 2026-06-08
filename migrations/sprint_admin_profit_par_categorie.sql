-- Ad BUD — Admin & profit décomposé par CATÉGORIE (MAT / MO / ST), par discipline.
--
-- Étape 2 du chantier « Paramètres financiers ». En plus du % par discipline
-- (pct_admin_<groupe>), on permet un % distinct par catégorie de coût au sein de
-- la discipline. NULL = non défini = HÉRITE du % discipline (rétrocompat stricte).
--
-- Hiérarchie de priorité (du + spécifique au + général) :
--   1. pct_admin_<groupe>_<cat>  (si défini)
--   2. pct_admin_<groupe>        (% discipline ; le champ « simple » écrit ici)
--   3. 0
--
-- Garantie rétrocompat : si AUCUN % catégorie n'est défini pour un groupe, le
-- calcul reste EXACTEMENT l'existant (sub_groupe × %discipline). Prouvé sur
-- Captation = 285 573,20 $ avant taxes (inchangé).
--
-- 12 colonnes NUMERIC NULLABLE. Tracké schema_migrations (joué une fois).
-- ADD COLUMN IF NOT EXISTS par sécurité.

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS pct_admin_conditions_mat   NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_conditions_mo    NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_conditions_st    NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_architecture_mat NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_architecture_mo  NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_architecture_st  NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_mecanique_mat    NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_mecanique_mo     NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_mecanique_st     NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_excavation_mat   NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_excavation_mo    NUMERIC NULL,
  ADD COLUMN IF NOT EXISTS pct_admin_excavation_st    NUMERIC NULL;
