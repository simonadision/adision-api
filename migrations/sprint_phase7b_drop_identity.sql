-- ════════════════════════════════════════════════════════════════════
-- Phase 7B — DROP des 19 colonnes d'IDENTITÉ de ad_budget.projets
-- ════════════════════════════════════════════════════════════════════
-- Clôture de l'arc « centralisation des infos projet ». L'identité projet
-- vit dans Ad HUB (app_hub.projects, source unique). Après Phase 6 (lecture
-- re-pointée) + Phase 7A (4 dernières lectures de `nom` re-pointées +
-- _ensure_schema neutralisé) + Deploy 3 (écritures retirées), AUCUN chemin
-- LIVE ne lit ni n'écrit ces colonnes -> elles sont mortes et supprimables.
--
-- Idempotent : DROP COLUMN IF EXISTS (rejouable sans effet). Transactionnel :
-- un seul ALTER (atomique) + le runner commit après. Tracké schema_migrations.
-- Les CHECK (type_batiment_check, superficie_m2_check) partent avec le DROP.
--
-- ROLLBACK (best-effort — les DONNÉES locales figées sont perdues ; OK, tout
-- est lu du hub). Types/defaults d'origine pour un retour structurel propre :
--   ALTER TABLE ad_budget.projets
--     ADD COLUMN IF NOT EXISTS nom                    VARCHAR(255),
--     ADD COLUMN IF NOT EXISTS nom_client             TEXT    DEFAULT '',
--     ADD COLUMN IF NOT EXISTS client                 VARCHAR(255),
--     ADD COLUMN IF NOT EXISTS type_batiment          VARCHAR(20),
--     ADD COLUMN IF NOT EXISTS region                 VARCHAR(50),
--     ADD COLUMN IF NOT EXISTS date_adjudication      DATE,
--     ADD COLUMN IF NOT EXISTS superficie_m2          NUMERIC(10,2),
--     ADD COLUMN IF NOT EXISTS date_debut             DATE,
--     ADD COLUMN IF NOT EXISTS date_fin               DATE,
--     ADD COLUMN IF NOT EXISTS adresse                VARCHAR(500),
--     ADD COLUMN IF NOT EXISTS description            TEXT,
--     ADD COLUMN IF NOT EXISTS numero_projet          TEXT    DEFAULT '',
--     ADD COLUMN IF NOT EXISTS contact_client         TEXT    DEFAULT '',
--     ADD COLUMN IF NOT EXISTS email_client           TEXT    DEFAULT '',
--     ADD COLUMN IF NOT EXISTS telephone_client       TEXT    DEFAULT '',
--     ADD COLUMN IF NOT EXISTS contact_entrepreneur   TEXT    DEFAULT '',
--     ADD COLUMN IF NOT EXISTS email_entrepreneur     TEXT    DEFAULT '',
--     ADD COLUMN IF NOT EXISTS telephone_entrepreneur TEXT    DEFAULT '',
--     ADD COLUMN IF NOT EXISTS logo_base64            TEXT    DEFAULT '';
--   (et ré-ajouter les CHECK type_batiment/superficie_m2 si besoin.)
-- ⚠ NE PAS réintroduire les ADD COLUMN dans _ensure_schema (neutralisé 7A).
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.projets
  DROP COLUMN IF EXISTS nom,
  DROP COLUMN IF EXISTS nom_client,
  DROP COLUMN IF EXISTS client,
  DROP COLUMN IF EXISTS type_batiment,
  DROP COLUMN IF EXISTS region,
  DROP COLUMN IF EXISTS date_adjudication,
  DROP COLUMN IF EXISTS superficie_m2,
  DROP COLUMN IF EXISTS date_debut,
  DROP COLUMN IF EXISTS date_fin,
  DROP COLUMN IF EXISTS adresse,
  DROP COLUMN IF EXISTS description,
  DROP COLUMN IF EXISTS numero_projet,
  DROP COLUMN IF EXISTS contact_client,
  DROP COLUMN IF EXISTS email_client,
  DROP COLUMN IF EXISTS telephone_client,
  DROP COLUMN IF EXISTS contact_entrepreneur,
  DROP COLUMN IF EXISTS email_entrepreneur,
  DROP COLUMN IF EXISTS telephone_entrepreneur,
  DROP COLUMN IF EXISTS logo_base64;
