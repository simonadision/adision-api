-- ════════════════════════════════════════════════════════════════════
-- Sprint Gabarits v2 — Hiérarchie à 3 NIVEAUX (Temps 1 : modèle + migration)
--
-- Avant : gabarit → gabarit_section (code CSI) → gabarit_ligne (2 niveaux).
-- Après : gabarit → gabarit_section (= DIVISION CSI, ex. "09 00 00")
--                 → gabarit_sous_sections (= SECTION CSI fine, ex. "09 91 00")  ← NOUVEAU
--                 → gabarit_ligne (rattachée à la SOUS-SECTION, plus à la division).
--
-- Nom de table gabarit_sections CONSERVÉ (= la division) pour limiter le blast
-- radius (FK, code applicatif). Le NOUVEAU niveau intermédiaire est
-- gabarit_sous_sections.
--
-- MIGRATION DES DONNÉES (IMPÉRATIF : préserver Gabarit 1 id=3 et ses 74 lignes) :
-- pour chaque section existante (= future division) SANS sous-section, on crée
-- 1 SOUS-SECTION PAR DÉFAUT reprenant le MÊME code CSI + libellé, et on y
-- rattache toutes ses lignes encore directement rattachées. Résultat : hiérarchie
-- valide immédiatement (Division → 1 sous-section par défaut → lignes), zéro
-- ligne orpheline. L'utilisateur réorganisera ensuite.
--
-- Aucune VUE dépendante de ces tables (vérifié via pg_depend → []), donc l'ALTER
-- / DROP COLUMN se fait sans DROP+recreate de vue. Tout est idempotent /
-- ré-exécution-sûr (IF [NOT] EXISTS + garde-fou colonne legacy) ; de plus le
-- runner (api.py _bootstrap_db) joue chaque fichier UNE fois, en transaction.
-- ════════════════════════════════════════════════════════════════════

-- 1. NOUVELLE table : sous-section (niveau 2), entre la division et les lignes.
--    CASCADE depuis la division → supprimer une division supprime ses sous-sections.
CREATE TABLE IF NOT EXISTS ad_budget.gabarit_sous_sections (
  id                 SERIAL PRIMARY KEY,
  gabarit_section_id INTEGER NOT NULL
                       REFERENCES ad_budget.gabarit_sections(id) ON DELETE CASCADE,
  code_csi           VARCHAR(50),
  libelle            VARCHAR(200) NOT NULL DEFAULT '',
  ordre              INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gabarit_sous_sections_section
  ON ad_budget.gabarit_sous_sections (gabarit_section_id);

-- 2. Rattachement des lignes à la SOUS-SECTION (niveau 3 → niveau 2).
--    On AJOUTE d'abord la colonne (nullable le temps du backfill), CASCADE.
ALTER TABLE ad_budget.gabarit_lignes
  ADD COLUMN IF NOT EXISTS gabarit_sous_section_id INTEGER
    REFERENCES ad_budget.gabarit_sous_sections(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_gabarit_lignes_sous_section
  ON ad_budget.gabarit_lignes (gabarit_sous_section_id);

-- 3. BACKFILL : 1 sous-section par défaut par section existante + déplacement
--    des lignes. Garde-fou : ne tourne que si la colonne legacy existe encore
--    (→ no-op après le DROP de l'étape 4, ré-exécution sûre). La condition
--    « section SANS sous-section » empêche tout doublon.
DO $$
DECLARE
  r        RECORD;
  v_ss_id  INTEGER;
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'ad_budget'
      AND table_name   = 'gabarit_lignes'
      AND column_name  = 'gabarit_section_id'
  ) THEN
    FOR r IN
      SELECT s.id, s.numero, s.nom_section
      FROM ad_budget.gabarit_sections s
      WHERE NOT EXISTS (
        SELECT 1 FROM ad_budget.gabarit_sous_sections ss
        WHERE ss.gabarit_section_id = s.id
      )
    LOOP
      INSERT INTO ad_budget.gabarit_sous_sections
        (gabarit_section_id, code_csi, libelle, ordre)
      VALUES (r.id, r.numero, COALESCE(r.nom_section, ''), 0)
      RETURNING id INTO v_ss_id;

      UPDATE ad_budget.gabarit_lignes
      SET gabarit_sous_section_id = v_ss_id
      WHERE gabarit_section_id = r.id
        AND gabarit_sous_section_id IS NULL;
    END LOOP;
  END IF;
END $$;

-- 4. Retrait du rattachement direct ligne→division (remplacé par ligne→sous-section).
--    IF EXISTS → ré-exécution sûre. (Index legacy dépendant retiré d'abord.)
DROP INDEX IF EXISTS ad_budget.idx_gabarit_lignes_section;
ALTER TABLE ad_budget.gabarit_lignes DROP COLUMN IF EXISTS gabarit_section_id;
