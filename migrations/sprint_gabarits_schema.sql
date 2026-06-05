-- ════════════════════════════════════════════════════════════════════
-- Sprint Gabarits — Bibliothèque de gabarits de budget Ad BUD
--
-- Un GABARIT est une TRAME réutilisable de lignes organisées en sections,
-- PRIVÉE par organisation (organization_id strict, pas de partage cross-org).
-- Il mémorise la STRUCTURE seulement — AUCUNE valeur (qté/prix/taux/montant) :
--   - une ligne 'manuelle' = description libre,
--   - une ligne 'ad_typ'   = code catalogue Ad TYP + description d'affichage.
-- À l'insertion dans un budget, les lignes ad_typ sont tarifées au catalogue
-- COURANT (apply-typ), les manuelles arrivent avec valeurs vides.
--
-- Idempotente (CREATE TABLE/INDEX IF NOT EXISTS). N'affecte AUCUN budget
-- existant (tables nouvelles, aucune ALTER, aucune vue dépendante).
-- ════════════════════════════════════════════════════════════════════

-- 1. Gabarit (en-tête) — scope privé par organisation.
CREATE TABLE IF NOT EXISTS ad_budget.gabarits (
  id              SERIAL PRIMARY KEY,
  organization_id UUID NOT NULL,
  nom             VARCHAR(200) NOT NULL,
  description     TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Index préfixé organization_id : tout listing est scopé par org.
CREATE INDEX IF NOT EXISTS idx_gabarits_org
  ON ad_budget.gabarits (organization_id);

-- 2. Sections d'un gabarit (ordonnées).
CREATE TABLE IF NOT EXISTS ad_budget.gabarit_sections (
  id           SERIAL PRIMARY KEY,
  gabarit_id   INTEGER NOT NULL
                 REFERENCES ad_budget.gabarits(id) ON DELETE CASCADE,
  nom_section  VARCHAR(200) NOT NULL DEFAULT '',
  numero       VARCHAR(50),
  ordre        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gabarit_sections_gabarit
  ON ad_budget.gabarit_sections (gabarit_id);

-- 3. Lignes d'une section (ordonnées). AUCUNE valeur (trame).
--    type='manuelle' → description seule ; type='ad_typ' → code_typ (catalogue)
--    + description (libellé d'affichage). code_typ NULL sauf si type='ad_typ'.
CREATE TABLE IF NOT EXISTS ad_budget.gabarit_lignes (
  id                 SERIAL PRIMARY KEY,
  gabarit_section_id INTEGER NOT NULL
                       REFERENCES ad_budget.gabarit_sections(id) ON DELETE CASCADE,
  ordre              INTEGER NOT NULL DEFAULT 0,
  type               VARCHAR(20) NOT NULL DEFAULT 'manuelle'
                       CHECK (type IN ('manuelle', 'ad_typ')),
  description        TEXT NOT NULL DEFAULT '',
  code_typ           VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS idx_gabarit_lignes_section
  ON ad_budget.gabarit_lignes (gabarit_section_id);
