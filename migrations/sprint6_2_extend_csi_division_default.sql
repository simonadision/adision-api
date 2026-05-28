-- ════════════════════════════════════════════════════════════════════
-- Adision Ad BUD — Sprint 6.2 : extension csi_division_default_metier
-- ════════════════════════════════════════════════════════════════════
--
-- La table ad_budget.csi_division_default_metier (mig sprint2_taux_horaires)
-- avait 19 mappings (01-12, 14, 20, 21, 22, 23, 25, 26) pointant tous
-- (pour la majorité) vers CHARPENTIER_C par défaut. Sprint 6.2 corrige
-- les mappings et ajoute les divisions manquantes (13, 27, 28, 31, 32, 33)
-- pour l'auto-fill du MO Taux $ d'Ad EST (et le maintien de l'auto-fill
-- existant côté Ad BUD).
--
-- Décisions Simon (28 mai 2026) :
--   - Métier par défaut = COMPAGNON (les apprentis ne sont jamais le défaut)
--   - Niveau de taux côté Ad EST = total_avant_admin (col 20, "Recommandé
--     soumission") — Ad BUD continue d'utiliser taux_col17 (rétrocompat)
--   - Division 00 : pas de mapping (pas de métier MO universel)
--
-- Codes BD vérifiés via codes_175_extracted.txt (Sprint 6 référentiel) :
--   - VITRIER_C        = 'Monteur mécanicien (vitrier)' Compagnon
--   - MECA_CHANTIER_C  = 'Mécanicien de chantier' Compagnon
--   - MECA_ASCENSEUR_C = 'Mécanicien d''ascenseurs' Compagnon
--   - MECA_PROFEU_C    = 'Mécanicien protection-incendie' Compagnon
--   - INSTALL_SECU_C   = 'Installateur de systèmes de sécurité' Compagnon
--   - OCC_MANOEUVRE_JOURN / OCC_MANOEUVRE_SPEC (occupations, pas métier)
--
-- Mapping appliqué (24 divisions, 00 exclu) :
--   01 → MANOEUVRE_JOURN  (occupation, exigences générales / manœuvre)
--   02 → MANOEUVRE_SPEC   (occupation, conditions existantes)
--   03-06, 09, 10, 12, 13 → CHARPENTIER_C (béton/maçonnerie/bois/finitions)
--   04 → BRIQUETEUR_C
--   07 → COUVREUR_C
--   08 → VITRIER_C
--   11 → MECA_CHANTIER_C
--   14 → MECA_ASCENSEUR_C
--   21 → MECA_PROFEU_C   (protection incendie)
--   22 → TUYAUTEUR_C     (plomberie)
--   23 → CHARPENTIER_C   (CVAC — défaut neutre car hétérogène)
--   25-27 → ELECTRICIEN_C (auto/élec/télécom)
--   28 → INSTALL_SECU_C  (sécurité électronique)
--   31, 32 → CHARPENTIER_C (excavation/aménagements — défaut neutre)
--   33 → TUYAUTEUR_C     (services publics enfouis)
--
-- Idempotent : INSERT ... ON CONFLICT (csi_division) DO UPDATE → rejouage
-- safe, écrase tous les mappings listés.
-- ════════════════════════════════════════════════════════════════════

-- Step 1 — UPSERT 24 divisions vers leur métier compagnon par défaut.
-- Le sous-select résout code → taux_id à l'exécution (échoue silencieusement
-- si le code n'existe pas en BD — détecté par le garde-fou Step 2).
DO $$
DECLARE
  v_mapping CONSTANT TEXT[][] := ARRAY[
    ARRAY['01', 'OCC_MANOEUVRE_JOURN'],
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
  v_row TEXT[];
  v_csi TEXT;
  v_code TEXT;
  v_taux_id INTEGER;
  v_missing_codes TEXT[] := ARRAY[]::TEXT[];
  v_upserted INTEGER := 0;
BEGIN
  FOREACH v_row SLICE 1 IN ARRAY v_mapping
  LOOP
    v_csi := v_row[1];
    v_code := v_row[2];

    SELECT id INTO v_taux_id
      FROM ad_budget.taux_horaires
     WHERE code = v_code AND actif = TRUE
     LIMIT 1;

    IF v_taux_id IS NULL THEN
      v_missing_codes := v_missing_codes || v_code;
      CONTINUE;
    END IF;

    INSERT INTO ad_budget.csi_division_default_metier
      (csi_division, taux_id, updated_by)
    VALUES (v_csi, v_taux_id, 'sprint6_2-2026-05-28')
    ON CONFLICT (csi_division) DO UPDATE
      SET taux_id    = EXCLUDED.taux_id,
          updated_by = EXCLUDED.updated_by;
    v_upserted := v_upserted + 1;
  END LOOP;

  RAISE NOTICE 'Sprint 6.2 — % / 24 mappings upsertés.', v_upserted;
  IF array_length(v_missing_codes, 1) > 0 THEN
    RAISE WARNING 'Codes métier absents de taux_horaires (mapping skip) : %',
      array_to_string(v_missing_codes, ', ');
  END IF;
END $$;

-- Step 2 — Garde-fou final : log de l'état post-migration.
DO $$
DECLARE
  v_total INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_total FROM ad_budget.csi_division_default_metier;
  RAISE NOTICE 'csi_division_default_metier post-Sprint 6.2 : % rows', v_total;
END $$;
