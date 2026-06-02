-- ════════════════════════════════════════════════════════════════════
-- Adision Ad BUD — Sprint 6.7 : 4 entrées section CSI (substitutions Simon)
-- ════════════════════════════════════════════════════════════════════
-- Complète sprint6_6 : les 2 libellés introuvables ("Menuisier", "Monteur
-- d'acier de structure") ont été tranchés par Simon en substitutions :
--   08 10 → CHARPENTIER_C (Charpentier-menuisier)  [ex « Menuisier »]
--   08 14 → CHARPENTIER_C
--   05 12 → MONTEUR_ASS_C (Monteur-assembleur)      [ex « Monteur d'acier de structure »]
--   05 50 → MONTEUR_ASS_C
--
-- Self-contained (CREATE TABLE IF NOT EXISTS) pour rester robuste quel que soit
-- l'ordre/état de sprint6_6. Résolution stricte du code contre taux_horaires
-- (actif) : skip + WARNING si introuvable (jamais d'orphelin). Idempotent
-- (ON CONFLICT DO UPDATE). Sans RAISE fatal. Seed de données uniquement —
-- Compagnon + Col 17, garde mo_taux_source, taux éditable : inchangés.
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_budget.csi_section_default_metier (
  csi_section  CHAR(5) PRIMARY KEY,
  taux_id      INTEGER NOT NULL REFERENCES ad_budget.taux_horaires(id) ON DELETE RESTRICT,
  updated_at   TIMESTAMP NOT NULL DEFAULT now(),
  updated_by   TEXT
);

DO $$
DECLARE
  v_map CONSTANT TEXT[][] := ARRAY[
    ARRAY['08 10','CHARPENTIER_C'],
    ARRAY['08 14','CHARPENTIER_C'],
    ARRAY['05 12','MONTEUR_ASS_C'],
    ARRAY['05 50','MONTEUR_ASS_C']
  ];
  v_row TEXT[]; v_key TEXT; v_code TEXT; v_id INT;
  v_missing TEXT[] := ARRAY[]::TEXT[]; v_n INT := 0;
BEGIN
  FOREACH v_row SLICE 1 IN ARRAY v_map LOOP
    v_key := v_row[1]; v_code := v_row[2];
    SELECT id INTO v_id FROM ad_budget.taux_horaires WHERE code = v_code AND actif = TRUE LIMIT 1;
    IF v_id IS NULL THEN v_missing := v_missing || v_code; CONTINUE; END IF;
    INSERT INTO ad_budget.csi_section_default_metier (csi_section, taux_id, updated_by)
    VALUES (v_key, v_id, 'sprint6_7-2026-06-02')
    ON CONFLICT (csi_section) DO UPDATE SET taux_id = EXCLUDED.taux_id, updated_by = EXCLUDED.updated_by;
    v_n := v_n + 1;
  END LOOP;
  RAISE NOTICE 'Sprint 6.7 — sections seedées: %', v_n;
  IF array_length(v_missing, 1) > 0 THEN
    RAISE WARNING 'Sprint 6.7 — codes métier introuvables (skippés): %', array_to_string(v_missing, ', ');
  END IF;
END $$;
