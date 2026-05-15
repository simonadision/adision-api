-- ════════════════════════════════════════════════════════════════════
-- Sprint 2 Ad BUD — D1 — Schéma taux_horaires + mapping CSI
-- ════════════════════════════════════════════════════════════════════
--
-- Crée deux nouvelles tables dans le schéma ad_budget :
--
-- 1) ad_budget.taux_horaires
--    Une ligne par niveau de qualification d'un métier ou occupation.
--    Référence statique consommée par :
--      - le helper backend `_resolve_taux_default(cur, section)` au moment
--        de l'INSERT d'une budget_lignes (push Ad VIU + ajout manuel)
--      - le frontend Ad BUD (TauxHorairePicker.jsx) — modal de sélection
--        au click sur la cellule taux_horaire
--      - le panneau super_admin Ad ADM (/admin/taux-horaires) pour CRUD
--
--    Source : grille ACQ « Taux horaires suggérés de la main-d'œuvre dans
--    l'industrie de la construction au Québec » 26 avril 2026 (col 17 du
--    PDF — Total Coût horaire main-d'œuvre, sans camions/outils).
--
--    Pour les NON-SYNDIQUÉS, la grille ACQ ne fournit pas de col 17. On y
--    stocke la col 20 (Total avant frais admin et profit) — cohérent avec
--    l'usage estimation où Simon intègre un coût horaire complet dans
--    budget_lignes.taux_horaire. Flagué dans la colonne `notes`.
--
-- 2) ad_budget.csi_division_default_metier
--    Table de mapping division CSI MasterFormat (2 chars) → taux_id.
--    Une PK unique sur csi_division garantit nativement « 1 défaut par
--    division ». Préféré à `csi_division TEXT[]` (indexation lourde) ou
--    aux rows dupliquées (codes laids + maintenance lourde).
--
--    Cas critique : Charpentier-menuisier est défaut pour 12 divisions
--    (01-12). Avec la table séparée, on a 12 rows dans le mapping qui
--    pointent toutes vers la même row dans taux_horaires (taux mis à
--    jour une seule fois, propagation auto).
--
-- ⚠️ NE PAS exécuter automatiquement — Simon exécute dans pgAdmin après
-- relecture (profil "Ad BUD Railway PROD", base "railway"... ou local
-- selon contexte). Pas de bloc transaction ici car c'est un CREATE TABLE
-- (DDL), implicitement commité. Pour rollback : DROP TABLE.
-- ════════════════════════════════════════════════════════════════════


-- ════════════════════════════════════════════════════════════════════
-- BLOC 1 — Contrôle AVANT (read-only)
-- ════════════════════════════════════════════════════════════════════

-- Vérif que le schema ad_budget existe (il devrait, c'est là que vivent
-- budget_lignes et projets)
SELECT EXISTS (
  SELECT 1 FROM information_schema.schemata WHERE schema_name='ad_budget'
) AS schema_ad_budget_exists;
-- Attendu : true

-- Vérif que les 2 tables N'EXISTENT PAS encore (sinon STOP)
SELECT table_name FROM information_schema.tables
WHERE table_schema='ad_budget'
  AND table_name IN ('taux_horaires','csi_division_default_metier');
-- Attendu : 0 rows


-- ════════════════════════════════════════════════════════════════════
-- BLOC 2 — CREATE TABLE taux_horaires
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE ad_budget.taux_horaires (
  id              SERIAL PRIMARY KEY,
  code            TEXT NOT NULL UNIQUE,
  metier          TEXT NOT NULL,
  qualification   TEXT,                              -- 'Compagnon' | 'Apprenti P1..P5' | 'Chef d''équipe' | etc. NULL = pas applicable (non-syndiqué)
  taux_col17      NUMERIC(8,2) NOT NULL,
  groupe          TEXT NOT NULL,
  ordre           INTEGER NOT NULL DEFAULT 0,        -- Ordre d'affichage dans le picker (intra-groupe)
  notes           TEXT,
  actif           BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_by      TEXT,
  CONSTRAINT taux_horaires_groupe_check
    CHECK (groupe IN ('metier','occupation','non_syndique'))
);

CREATE INDEX idx_taux_horaires_groupe ON ad_budget.taux_horaires(groupe);
CREATE INDEX idx_taux_horaires_actif  ON ad_budget.taux_horaires(actif) WHERE actif = TRUE;
CREATE INDEX idx_taux_horaires_ordre  ON ad_budget.taux_horaires(groupe, ordre);

COMMENT ON TABLE ad_budget.taux_horaires IS
  'Référence statique des taux horaires CCQ + non-syndiqués. Source grille ACQ 2026-04-26.';
COMMENT ON COLUMN ad_budget.taux_horaires.taux_col17 IS
  'Métiers/occupations: col 17 grille ACQ (Total Coût horaire main-d''œuvre). Non-syndiqués: col 20 (Total avant frais admin).';
COMMENT ON COLUMN ad_budget.taux_horaires.code IS
  'Code stable cross-environnements (utilisé par migrations + propagation prod). Cf. seed.';


-- ════════════════════════════════════════════════════════════════════
-- BLOC 3 — CREATE TABLE csi_division_default_metier
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE ad_budget.csi_division_default_metier (
  csi_division   CHAR(2) PRIMARY KEY,
  taux_id        INTEGER NOT NULL
                 REFERENCES ad_budget.taux_horaires(id)
                 ON DELETE RESTRICT,
  updated_at     TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_by     TEXT
);

CREATE INDEX idx_csi_division_default_taux_id
  ON ad_budget.csi_division_default_metier(taux_id);

COMMENT ON TABLE ad_budget.csi_division_default_metier IS
  'Mapping division CSI MasterFormat (2 chars) -> métier compagnon par défaut. Consommé par helper _resolve_taux_default à l''INSERT d''une budget_lignes.';


-- ════════════════════════════════════════════════════════════════════
-- BLOC 4 — Trigger updated_at (cosmétique mais utile pour audit)
-- ════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION ad_budget.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_taux_horaires_updated_at
  BEFORE UPDATE ON ad_budget.taux_horaires
  FOR EACH ROW EXECUTE FUNCTION ad_budget.set_updated_at();

CREATE TRIGGER trg_csi_div_default_updated_at
  BEFORE UPDATE ON ad_budget.csi_division_default_metier
  FOR EACH ROW EXECUTE FUNCTION ad_budget.set_updated_at();


-- ════════════════════════════════════════════════════════════════════
-- BLOC 5 — Contrôle APRÈS
-- ════════════════════════════════════════════════════════════════════

-- 5.1 — Les 2 tables doivent exister
SELECT table_name FROM information_schema.tables
WHERE table_schema='ad_budget'
  AND table_name IN ('taux_horaires','csi_division_default_metier')
ORDER BY table_name;
-- Attendu : 2 rows

-- 5.2 — Colonnes taux_horaires
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema='ad_budget' AND table_name='taux_horaires'
ORDER BY ordinal_position;
-- Attendu : 11 colonnes (id, code, metier, qualification, taux_col17,
--   groupe, ordre, notes, actif, updated_at, updated_by)

-- 5.3 — Indexes et contraintes
SELECT indexname FROM pg_indexes
WHERE schemaname='ad_budget'
  AND tablename IN ('taux_horaires','csi_division_default_metier')
ORDER BY tablename, indexname;
-- Attendu : 6 lignes
--   csi_division_default_metier_pkey
--   idx_csi_division_default_taux_id
--   idx_taux_horaires_actif
--   idx_taux_horaires_groupe
--   idx_taux_horaires_ordre
--   taux_horaires_code_key
--   taux_horaires_pkey
-- (7 actually — pkey + code_key + 3 idx_ pour taux_horaires; pkey + 1 idx_ pour mapping)

-- 5.4 — Triggers
SELECT trigger_name FROM information_schema.triggers
WHERE event_object_schema='ad_budget'
  AND event_object_table IN ('taux_horaires','csi_division_default_metier')
ORDER BY trigger_name;
-- Attendu : 2 lignes

-- 5.5 — Tables encore vides
SELECT
  (SELECT COUNT(*) FROM ad_budget.taux_horaires)                     AS n_taux,
  (SELECT COUNT(*) FROM ad_budget.csi_division_default_metier)       AS n_mapping;
-- Attendu : 0 / 0  (le seed se fait dans sprint2_taux_horaires_seed.sql)
