-- ════════════════════════════════════════════════════════════════════
-- Adision Ad BUD — Sprint 6.3 : recalibrage csi_division_default_metier
-- ════════════════════════════════════════════════════════════════════
--
-- Règles définitives Simon (28 mai 2026) :
--   1. col 17 (Coût horaire MO) toujours, jamais col 20 — frontend +
--      mig 038 côté adision-est-api gèrent ce volet (re-traitement
--      des items existants en BD).
--   2. Division 01 (Exigences générales / admin chantier) = AUCUN
--      taux par défaut. Cette migration DELETE la row du mapping pour
--      que /budget/csi-division-defaults n'expose pas la div 01.
--   3. Manœuvre uniquement sur div 02 (Conditions existantes /
--      Démolition).
--   4. Ambiguïté → CHARPENTIER_C.
--
-- Mig précédente sprint6_2 avait mappé div 01 → OCC_MANOEUVRE_JOURN.
-- On défait ça. Les 23 autres mappings sont confirmés en UPSERT
-- défensif (no-op si déjà à la bonne valeur).
--
-- Idempotent : DELETE WHERE csi_division='01' est no-op au re-run,
-- INSERT ... ON CONFLICT DO UPDATE écrase à la même valeur.
-- ════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  v_mapping CONSTANT TEXT[][] := ARRAY[
    -- ⚠ Division 01 RETIRÉE intentionnellement.
    ARRAY['02', 'OCC_MANOEUVRE_SPEC'],
    ARRAY['03', 'CHARPENTIER_C'],
    ARRAY['04', 'BRIQUETEUR_C'],
    ARRAY['05', 'CHARPENTIER_C'],
    ARRAY['06', 'CHARPENTIER_C'],
    ARRAY['07', 'COUVREUR_C'],
    ARRAY['08', 'VITRIER_C'],
    ARRAY['09', 'CHARPENTIER_C'],
    ARRAY['10', 'CHARPENTIER_C'],
    ARRAY['11', 'MECA_CHANTIER_C'],
    ARRAY['12', 'CHARPENTIER_C'],
    ARRAY['13', 'CHARPENTIER_C'],
    ARRAY['14', 'MECA_ASCENSEUR_C'],
    ARRAY['21', 'MECA_PROFEU_C'],
    ARRAY['22', 'TUYAUTEUR_C'],
    ARRAY['23', 'CHARPENTIER_C'],
    ARRAY['25', 'ELECTRICIEN_C'],
    ARRAY['26', 'ELECTRICIEN_C'],
    ARRAY['27', 'ELECTRICIEN_C'],
    ARRAY['28', 'INSTALL_SECU_C'],
    ARRAY['31', 'CHARPENTIER_C'],
    ARRAY['32', 'CHARPENTIER_C'],
    ARRAY['33', 'TUYAUTEUR_C']
  ];
  v_row         TEXT[];
  v_csi         TEXT;
  v_code        TEXT;
  v_taux_id     INTEGER;
  v_missing     TEXT[] := ARRAY[]::TEXT[];
  v_upserted    INTEGER := 0;
  v_deleted_01  INTEGER := 0;
  v_total_after INTEGER;
BEGIN
  -- Step 1 — Retirer le mapping div 01 (règle Sprint 6.3).
  DELETE FROM ad_budget.csi_division_default_metier
   WHERE csi_division = '01';
  GET DIAGNOSTICS v_deleted_01 = ROW_COUNT;

  -- Step 2 — UPSERT défensif des 23 autres mappings.
  FOREACH v_row SLICE 1 IN ARRAY v_mapping
  LOOP
    v_csi  := v_row[1];
    v_code := v_row[2];

    SELECT id INTO v_taux_id
      FROM ad_budget.taux_horaires
     WHERE code = v_code AND actif = TRUE
     LIMIT 1;

    IF v_taux_id IS NULL THEN
      v_missing := v_missing || v_code;
      CONTINUE;
    END IF;

    INSERT INTO ad_budget.csi_division_default_metier
      (csi_division, taux_id, updated_by)
    VALUES (v_csi, v_taux_id, 'sprint6_3-2026-05-28')
    ON CONFLICT (csi_division) DO UPDATE
      SET taux_id    = EXCLUDED.taux_id,
          updated_by = EXCLUDED.updated_by;
    v_upserted := v_upserted + 1;
  END LOOP;

  SELECT COUNT(*) INTO v_total_after FROM ad_budget.csi_division_default_metier;

  RAISE NOTICE 'Sprint 6.3 — DELETE div 01 : % rows', v_deleted_01;
  RAISE NOTICE 'Sprint 6.3 — UPSERT 23 autres divs : % réussis', v_upserted;
  RAISE NOTICE 'Sprint 6.3 — total mapping post-migration : % rows', v_total_after;
  IF array_length(v_missing, 1) > 0 THEN
    RAISE WARNING 'Codes métier absents : %', array_to_string(v_missing, ', ');
  END IF;
END $$;
