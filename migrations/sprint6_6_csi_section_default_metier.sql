-- ════════════════════════════════════════════════════════════════════
-- Adision Ad BUD — Sprint 6.6 : mapping CSI par SECTION (4 chiffres) + repli division
-- ════════════════════════════════════════════════════════════════════
-- Ajoute un niveau SECTION (clé "NN NN", 5 chars) PRIORITAIRE sur la division.
-- Cascade côté Ad EST : section exacte (csi_section) → division (csi_division) →
-- rien (manuel). On NE supprime PAS le mapping division existant : il devient le
-- repli. Auto-fill inchangé (Compagnon + Col 17, garde mo_taux_source).
--
-- Résolution stricte des libellés contre ad_budget.taux_horaires (code _C /
-- OCC_*), skip + WARNING si introuvable (jamais d'orphelin). Idempotent :
-- ON CONFLICT DO UPDATE. Sans RAISE fatal.
--
-- INTROUVABLES connus (NON seedés, à trancher par Simon) :
--   05 12 / 05 50 « Monteur d'acier de structure » → candidat MONTEUR_ASS_C (Monteur-assembleur)
--   08 10 / 08 14 « Menuisier »                    → candidat CHARPENTIER_C (Charpentier-menuisier)
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_budget.csi_section_default_metier (
  csi_section  CHAR(5) PRIMARY KEY,          -- format "NN NN" (ex. "09 91")
  taux_id      INTEGER NOT NULL REFERENCES ad_budget.taux_horaires(id) ON DELETE RESTRICT,
  updated_at   TIMESTAMP NOT NULL DEFAULT now(),
  updated_by   TEXT
);
CREATE INDEX IF NOT EXISTS idx_csi_section_default_taux_id
  ON ad_budget.csi_section_default_metier(taux_id);
COMMENT ON TABLE ad_budget.csi_section_default_metier IS
  'Mapping section CSI (4 chiffres "NN NN") -> métier par défaut. PRIORITAIRE sur csi_division_default_metier (repli). Consommé par l''auto-fill MO Taux $ d''Ad EST (cascade section->division->manuel).';

DO $$
DECLARE
  v_sections CONSTANT TEXT[][] := ARRAY[
    ARRAY['09 21','POSEUR_SYSINT_C'], ARRAY['09 29','POSEUR_SYSINT_C'], ARRAY['09 51','POSEUR_SYSINT_C'],
    ARRAY['09 23','PLATRIER_C'],      ARRAY['09 30','CARRELEUR_C'],     ARRAY['09 65','POSEUR_SOUPLES_C'],
    ARRAY['09 91','PEINTRE_C'],       ARRAY['07 21','CALORIFUGEUR_C'],  ARRAY['07 50','COUVREUR_C'],
    ARRAY['07 46','CHARPENTIER_C'],   ARRAY['07 81','CHARPENTIER_C'],   ARRAY['07 90','CHARPENTIER_C'],
    ARRAY['07 92','CHARPENTIER_C'],   ARRAY['08 11','CHARPENTIER_C'],   ARRAY['08 50','VITRIER_C'],
    ARRAY['08 80','VITRIER_C'],       ARRAY['23 07','CALORIFUGEUR_C']
  ];
  v_divisions CONSTANT TEXT[][] := ARRAY[
    ARRAY['02','OCC_MANOEUVRE_SPEC'], ARRAY['03','CIMENTIER_C'], ARRAY['04','BRIQUETEUR_C'],
    ARRAY['06','CHARPENTIER_C'],      ARRAY['21','TUYAUTEUR_C'], ARRAY['22','TUYAUTEUR_C'],
    ARRAY['23','FERBLANTIER_C'],      ARRAY['26','ELECTRICIEN_C']
  ];
  v_row TEXT[]; v_key TEXT; v_code TEXT; v_id INT;
  v_missing TEXT[] := ARRAY[]::TEXT[];
  v_sec INT := 0; v_div INT := 0;
BEGIN
  FOREACH v_row SLICE 1 IN ARRAY v_sections LOOP
    v_key := v_row[1]; v_code := v_row[2];
    SELECT id INTO v_id FROM ad_budget.taux_horaires WHERE code = v_code AND actif = TRUE LIMIT 1;
    IF v_id IS NULL THEN v_missing := v_missing || v_code; CONTINUE; END IF;
    INSERT INTO ad_budget.csi_section_default_metier (csi_section, taux_id, updated_by)
    VALUES (v_key, v_id, 'sprint6_6-2026-06-02')
    ON CONFLICT (csi_section) DO UPDATE SET taux_id = EXCLUDED.taux_id, updated_by = EXCLUDED.updated_by;
    v_sec := v_sec + 1;
  END LOOP;

  FOREACH v_row SLICE 1 IN ARRAY v_divisions LOOP
    v_key := v_row[1]; v_code := v_row[2];
    SELECT id INTO v_id FROM ad_budget.taux_horaires WHERE code = v_code AND actif = TRUE LIMIT 1;
    IF v_id IS NULL THEN v_missing := v_missing || v_code; CONTINUE; END IF;
    INSERT INTO ad_budget.csi_division_default_metier (csi_division, taux_id, updated_by)
    VALUES (v_key, v_id, 'sprint6_6-2026-06-02')
    ON CONFLICT (csi_division) DO UPDATE SET taux_id = EXCLUDED.taux_id, updated_by = EXCLUDED.updated_by;
    v_div := v_div + 1;
  END LOOP;

  RAISE NOTICE 'Sprint 6.6 — sections seedées: %, divisions upsert: %', v_sec, v_div;
  IF array_length(v_missing, 1) > 0 THEN
    RAISE WARNING 'Sprint 6.6 — codes métier introuvables (skippés): %', array_to_string(v_missing, ', ');
  END IF;
END $$;
