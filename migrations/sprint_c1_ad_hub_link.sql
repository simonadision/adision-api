-- ════════════════════════════════════════════════════════════════════
-- Sprint C1 — Lien Ad BUD ↔ Ad HUB sur les projets
--
-- Ajoute une référence applicative depuis ad_budget.projets vers
-- app_hub.projects.id (côté backend adision-app-api). Pas de FK SQL
-- cross-base : les 2 DBs Railway sont distinctes (projets adision-bud
-- vs adision-app), donc une contrainte référentielle SQL est impossible.
-- L'intégrité est validée applicativement via hub_service.fetch_project.
--
-- Décisions produit verrouillées (cf. rapport investigation) :
--   (a) Atomicité HUB-first + rollback applicatif si Ad BUD échoue
--   (A) Discipline NULL sur upload direct (Catégorie seule)
--   (α) AdHubDocSelector étendu avec prop multi=false par défaut
--   (I) Logo Ad BUD reste base64 in-DB
--
-- Type INTEGER : aligné sur app_hub.projects.id (BIGSERIAL → integer).
-- Évite le piège BIGINT vs UUID observé sur ad_viu.analyses.
-- ad_hub_document_id (cf. mig Ad VIU 032).
-- ════════════════════════════════════════════════════════════════════

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'ad_budget'
      AND table_name = 'projets'
      AND column_name = 'ad_hub_project_id'
  ) THEN
    ALTER TABLE ad_budget.projets
      ADD COLUMN ad_hub_project_id INTEGER;

    COMMENT ON COLUMN ad_budget.projets.ad_hub_project_id IS
      'Référence applicative vers app_hub.projects.id (DB séparée). '
      'Pas de FK SQL cross-base. NULL = projet Ad BUD sans lien Ad HUB '
      '(rétro-compat). NON NULL = projet créé en mode A (HUB existant) '
      'ou B (HUB créé en même temps que BUD, cf. Sprint C1).';
  END IF;
END$$;

-- Index partiel : seuls les projets liés sont indexés. Permet des
-- requêtes inverses rapides (« quel projet Ad BUD est lié à ce HUB ? »)
-- sans pénaliser les projets sans lien.
CREATE INDEX IF NOT EXISTS idx_projets_ad_hub_project_id
  ON ad_budget.projets (ad_hub_project_id)
  WHERE ad_hub_project_id IS NOT NULL;
