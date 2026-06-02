-- ════════════════════════════════════════════════════════════════════
-- Adision Ad BUD — Sprint 6.5 : mapping csi_division_default_metier (30–33)
-- ════════════════════════════════════════════════════════════════════
-- Migration de DONNÉES uniquement (table déjà créée ; schéma inchangé).
-- Mécanique auto-fill TAUX $ d'Ad EST INCHANGÉE : niveau Compagnon + Col 17
-- (taux_col17), garde mo_taux_source:auto_division. Le taux auto-rempli reste
-- ÉDITABLE côté front (rien verrouillé ici). AUCUNE suppression de mapping.
--
-- Résolution stricte (codes vérifiés contre ad_budget.taux_horaires, actif) :
--   30 → OP_PELLES_AA_C  « Opérateur de pelles mécaniques AA » (85,70 $, metier)
--   31 → OP_PELLES_AA_C  (remplace son CHARPENTIER_C actuel)
--   32 → OCC_MANOEUVRE_SPEC « Manœuvre spécialisé » (71,02 $) — remplace Charpentier
--   33 → OCC_MANOEUVRE_SPEC « Manœuvre spécialisé » — remplace Tuyauteur
--
-- Note : « Manœuvre spécialisé » existe UNIQUEMENT comme OCCUPATION
-- (OCC_MANOEUVRE_SPEC, pas de palier Compagnon/_C — les occupations ne sont pas
-- paliérisées) ; elle a bien un taux_col17 (71,02 $) et est déjà utilisée dans
-- le mapping (div 02) → cible réelle, pas un orphelin. 20 (Calorifugeur) et 28
-- (Installateur sécurité) NON touchées. Aucune autre division modifiée.
--
-- Jamais d'orphelin : skip + WARNING si un code est introuvable.
-- Idempotent : ON CONFLICT (csi_division) DO UPDATE → ré-exécution no-op.
-- ════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  v_mapping CONSTANT TEXT[][] := ARRAY[
    ARRAY['30', 'OP_PELLES_AA_C'],
    ARRAY['31', 'OP_PELLES_AA_C'],
    ARRAY['32', 'OCC_MANOEUVRE_SPEC'],
    ARRAY['33', 'OCC_MANOEUVRE_SPEC']
  ];
  v_row      TEXT[];
  v_csi      TEXT;
  v_code     TEXT;
  v_taux_id  INTEGER;
  v_missing  TEXT[] := ARRAY[]::TEXT[];
  v_upserted INTEGER := 0;
BEGIN
  FOREACH v_row SLICE 1 IN ARRAY v_mapping
  LOOP
    v_csi  := v_row[1];
    v_code := v_row[2];

    SELECT id INTO v_taux_id
      FROM ad_budget.taux_horaires
     WHERE code = v_code AND actif = TRUE
     LIMIT 1;

    IF v_taux_id IS NULL THEN
      v_missing := v_missing || v_code;   -- jamais d'orphelin : on skip
      CONTINUE;
    END IF;

    INSERT INTO ad_budget.csi_division_default_metier
      (csi_division, taux_id, updated_by)
    VALUES (v_csi, v_taux_id, 'sprint6_5-2026-06-02')
    ON CONFLICT (csi_division) DO UPDATE
      SET taux_id    = EXCLUDED.taux_id,
          updated_by = EXCLUDED.updated_by;
    v_upserted := v_upserted + 1;
  END LOOP;

  RAISE NOTICE 'Sprint 6.5 — UPSERT divisions 30/31/32/33 : % réussis', v_upserted;
  IF array_length(v_missing, 1) > 0 THEN
    RAISE WARNING 'Sprint 6.5 — codes métier introuvables (skippés, non seedés) : %',
      array_to_string(v_missing, ', ');
  END IF;
END $$;
