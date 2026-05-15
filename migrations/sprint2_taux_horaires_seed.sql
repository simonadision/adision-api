-- ════════════════════════════════════════════════════════════════════
-- Sprint 2 Ad BUD — D1 — Seed taux_horaires + mapping CSI
-- ════════════════════════════════════════════════════════════════════
--
-- À exécuter APRÈS sprint2_taux_horaires_schema.sql (les tables doivent
-- exister + être vides).
--
-- Source : grille ACQ « Taux horaires suggérés de la main-d'œuvre dans
-- l'industrie de la construction au Québec » 26 avril 2026.
--
-- Conventions de codes :
--   - Métiers compagnons : <METIER>_C
--   - Métiers apprentis  : <METIER>_A<N>  (N = période 1..5)
--   - Chefs              : CHEF_<TYPE>_CHARP
--   - Occupations        : OCC_<NOM>
--   - Non-syndiqués      : NS_<NOM>
--
-- Total attendu : 137 rows métiers + 29 occupations + 9 non-syndiqués = 175.
-- Mapping CSI : 19 rows (12 pour Charpentier-menuisier + 7 spécialisés).
--
-- ⚠️ Exécuter UN BLOC À LA FOIS dans pgAdmin. NE PAS lancer tout d'un coup.
-- Transaction explicite BEGIN/COMMIT autour des INSERT.
-- ════════════════════════════════════════════════════════════════════


-- ════════════════════════════════════════════════════════════════════
-- BLOC 1 — Contrôle AVANT (read-only)
-- ════════════════════════════════════════════════════════════════════

-- Les tables doivent exister et être vides
SELECT
  (SELECT COUNT(*) FROM ad_budget.taux_horaires)                   AS n_taux_avant,
  (SELECT COUNT(*) FROM ad_budget.csi_division_default_metier)     AS n_mapping_avant;
-- Attendu : 0 / 0


-- ════════════════════════════════════════════════════════════════════
-- BLOC 2 — INSERT taux_horaires (métiers + occupations + non-syndiqués)
-- ════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── MÉTIERS — compagnons + apprentis ─────────────────────────────────
INSERT INTO ad_budget.taux_horaires (code, metier, qualification, taux_col17, groupe, ordre, updated_by) VALUES
  ('BRIQUETEUR_C',         'Briqueteur-maçon',                  'Compagnon',           84.60, 'metier', 110, 'seed-2026-05-15'),
  ('BRIQUETEUR_A1',        'Briqueteur-maçon',                  'Apprenti période 1',  53.87, 'metier', 111, 'seed-2026-05-15'),
  ('BRIQUETEUR_A2',        'Briqueteur-maçon',                  'Apprenti période 2',  61.32, 'metier', 112, 'seed-2026-05-15'),
  ('BRIQUETEUR_A3',        'Briqueteur-maçon',                  'Apprenti période 3',  72.51, 'metier', 113, 'seed-2026-05-15'),
  ('CALORIFUGEUR_C',       'Calorifugeur',                      'Compagnon',           83.76, 'metier', 120, 'seed-2026-05-15'),
  ('CALORIFUGEUR_A1',      'Calorifugeur',                      'Apprenti période 1',  53.34, 'metier', 121, 'seed-2026-05-15'),
  ('CALORIFUGEUR_A2',      'Calorifugeur',                      'Apprenti période 2',  60.70, 'metier', 122, 'seed-2026-05-15'),
  ('CALORIFUGEUR_A3',      'Calorifugeur',                      'Apprenti période 3',  71.78, 'metier', 123, 'seed-2026-05-15'),
  ('CARRELEUR_C',          'Carreleur',                         'Compagnon',           84.58, 'metier', 130, 'seed-2026-05-15'),
  ('CARRELEUR_A1',         'Carreleur',                         'Apprenti période 1',  53.83, 'metier', 131, 'seed-2026-05-15'),
  ('CARRELEUR_A2',         'Carreleur',                         'Apprenti période 2',  61.30, 'metier', 132, 'seed-2026-05-15'),
  ('CARRELEUR_A3',         'Carreleur',                         'Apprenti période 3',  72.47, 'metier', 133, 'seed-2026-05-15'),
  ('CHARPENTIER_C',        'Charpentier-menuisier',             'Compagnon',           84.64, 'metier', 140, 'seed-2026-05-15'),
  ('CHARPENTIER_A1',       'Charpentier-menuisier',             'Apprenti période 1',  53.87, 'metier', 141, 'seed-2026-05-15'),
  ('CHARPENTIER_A2',       'Charpentier-menuisier',             'Apprenti période 2',  61.33, 'metier', 142, 'seed-2026-05-15'),
  ('CHARPENTIER_A3',       'Charpentier-menuisier',             'Apprenti période 3',  72.54, 'metier', 143, 'seed-2026-05-15'),
  ('CHAUDRONNIER_C',       'Chaudronnier',                      'Compagnon',           83.97, 'metier', 150, 'seed-2026-05-15'),
  ('CHAUDRONNIER_A1',      'Chaudronnier',                      'Apprenti période 1',  53.54, 'metier', 151, 'seed-2026-05-15'),
  ('CHAUDRONNIER_A2',      'Chaudronnier',                      'Apprenti période 2',  60.91, 'metier', 152, 'seed-2026-05-15'),
  ('CHAUDRONNIER_A3',      'Chaudronnier',                      'Apprenti période 3',  71.97, 'metier', 153, 'seed-2026-05-15'),
  ('CIMENTIER_C',          'Cimentier-applicateur',             'Compagnon',           82.91, 'metier', 160, 'seed-2026-05-15'),
  ('CIMENTIER_A1',         'Cimentier-applicateur',             'Apprenti période 1',  60.16, 'metier', 161, 'seed-2026-05-15'),
  ('CIMENTIER_A2',         'Cimentier-applicateur',             'Apprenti période 2',  71.08, 'metier', 162, 'seed-2026-05-15'),
  ('COUVREUR_C',           'Couvreur',                          'Compagnon',           87.82, 'metier', 170, 'seed-2026-05-15'),
  ('COUVREUR_A1',          'Couvreur',                          'Apprenti période 1',  63.73, 'metier', 171, 'seed-2026-05-15'),
  ('COUVREUR_A2',          'Couvreur',                          'Apprenti période 2',  75.34, 'metier', 172, 'seed-2026-05-15'),
  ('ELECTRICIEN_C',        'Électricien',                       'Compagnon',           86.02, 'metier', 180, 'seed-2026-05-15'),
  ('ELECTRICIEN_A1',       'Électricien',                       'Apprenti période 1',  47.46, 'metier', 181, 'seed-2026-05-15'),
  ('ELECTRICIEN_A2',       'Électricien',                       'Apprenti période 2',  54.98, 'metier', 182, 'seed-2026-05-15'),
  ('ELECTRICIEN_A3',       'Électricien',                       'Apprenti période 3',  62.50, 'metier', 183, 'seed-2026-05-15'),
  ('ELECTRICIEN_A4',       'Électricien',                       'Apprenti période 4',  73.79, 'metier', 184, 'seed-2026-05-15'),
  ('FERBLANTIER_C',        'Ferblantier',                       'Compagnon',           85.12, 'metier', 190, 'seed-2026-05-15'),
  ('FERBLANTIER_A1',       'Ferblantier',                       'Apprenti période 1',  54.14, 'metier', 191, 'seed-2026-05-15'),
  ('FERBLANTIER_A2',       'Ferblantier',                       'Apprenti période 2',  61.66, 'metier', 192, 'seed-2026-05-15'),
  ('FERBLANTIER_A3',       'Ferblantier',                       'Apprenti période 3',  72.93, 'metier', 193, 'seed-2026-05-15'),
  ('ARMATURE_C',           'Poseur d''armature du béton',       'Compagnon',           87.29, 'metier', 200, 'seed-2026-05-15'),
  ('ARMATURE_A',           'Poseur d''armature du béton',       'Apprenti',            74.83, 'metier', 201, 'seed-2026-05-15'),
  ('FRIGORISTE_HALO',      'Frigoriste (halocarbure)',          'Compagnon',           87.66, 'metier', 210, 'seed-2026-05-15'),
  ('FRIGORISTE_C',         'Frigoriste',                        'Compagnon',           83.97, 'metier', 220, 'seed-2026-05-15'),
  ('FRIGORISTE_A1',        'Frigoriste',                        'Apprenti période 1',  46.23, 'metier', 221, 'seed-2026-05-15'),
  ('FRIGORISTE_A2',        'Frigoriste',                        'Apprenti période 2',  53.60, 'metier', 222, 'seed-2026-05-15'),
  ('FRIGORISTE_A3',        'Frigoriste',                        'Apprenti période 3',  60.95, 'metier', 223, 'seed-2026-05-15'),
  ('FRIGORISTE_A4',        'Frigoriste',                        'Apprenti période 4',  72.01, 'metier', 224, 'seed-2026-05-15'),
  ('GRUTIER_A_C',          'Grutier classe A',                  'Compagnon',           83.77, 'metier', 230, 'seed-2026-05-15'),
  ('GRUTIER_A_A1',         'Grutier classe A',                  'Apprenti période 1',  60.58, 'metier', 231, 'seed-2026-05-15'),
  ('GRUTIER_A_A2',         'Grutier classe A',                  'Apprenti période 2',  71.72, 'metier', 232, 'seed-2026-05-15'),
  ('GRUTIER_B_C',          'Grutier classe B',                  'Compagnon',           80.55, 'metier', 240, 'seed-2026-05-15'),
  ('GRUTIER_B_A1',         'Grutier classe B',                  'Apprenti période 1',  58.34, 'metier', 241, 'seed-2026-05-15'),
  ('GRUTIER_B_A2',         'Grutier classe B',                  'Apprenti période 2',  68.98, 'metier', 242, 'seed-2026-05-15'),
  ('INSTALL_SECU_C',       'Installateur de systèmes de sécurité', 'Compagnon',        70.45, 'metier', 250, 'seed-2026-05-15'),
  ('INSTALL_SECU_A1',      'Installateur de systèmes de sécurité', 'Apprenti période 1', 45.51, 'metier', 251, 'seed-2026-05-15'),
  ('INSTALL_SECU_A2',      'Installateur de systèmes de sécurité', 'Apprenti période 2', 51.52, 'metier', 252, 'seed-2026-05-15'),
  ('INSTALL_SECU_A3',      'Installateur de systèmes de sécurité', 'Apprenti période 3', 60.53, 'metier', 253, 'seed-2026-05-15'),
  ('MECA_ASCENSEUR_C',     'Mécanicien d''ascenseurs',          'Compagnon',           91.93, 'metier', 260, 'seed-2026-05-15'),
  ('MECA_ASCENSEUR_A1',    'Mécanicien d''ascenseurs',          'Apprenti période 1',  49.81, 'metier', 261, 'seed-2026-05-15'),
  ('MECA_ASCENSEUR_A2',    'Mécanicien d''ascenseurs',          'Apprenti période 2',  58.05, 'metier', 262, 'seed-2026-05-15'),
  ('MECA_ASCENSEUR_A3',    'Mécanicien d''ascenseurs',          'Apprenti période 3',  66.30, 'metier', 263, 'seed-2026-05-15'),
  ('MECA_ASCENSEUR_A4',    'Mécanicien d''ascenseurs',          'Apprenti période 4',  78.65, 'metier', 264, 'seed-2026-05-15'),
  ('MECA_ASCENSEUR_A5',    'Mécanicien d''ascenseurs',          'Apprenti période 5',  78.65, 'metier', 265, 'seed-2026-05-15'),
  ('MECA_CHANTIER_C',      'Mécanicien de chantier',            'Compagnon',           84.03, 'metier', 270, 'seed-2026-05-15'),
  ('MECA_CHANTIER_A1',     'Mécanicien de chantier',            'Apprenti période 1',  53.60, 'metier', 271, 'seed-2026-05-15'),
  ('MECA_CHANTIER_A2',     'Mécanicien de chantier',            'Apprenti période 2',  60.98, 'metier', 272, 'seed-2026-05-15'),
  ('MECA_CHANTIER_A3',     'Mécanicien de chantier',            'Apprenti période 3',  72.05, 'metier', 273, 'seed-2026-05-15'),
  ('MECA_LOURDES_C',       'Mécanicien de machines lourdes',    'Compagnon',           81.93, 'metier', 280, 'seed-2026-05-15'),
  ('MECA_LOURDES_A1',      'Mécanicien de machines lourdes',    'Apprenti période 1',  52.40, 'metier', 281, 'seed-2026-05-15'),
  ('MECA_LOURDES_A2',      'Mécanicien de machines lourdes',    'Apprenti période 2',  59.56, 'metier', 282, 'seed-2026-05-15'),
  ('MECA_LOURDES_A3',      'Mécanicien de machines lourdes',    'Apprenti période 3',  70.29, 'metier', 283, 'seed-2026-05-15'),
  ('MECA_PROFEU_C',        'Mécanicien protection-incendie',    'Compagnon',           84.15, 'metier', 290, 'seed-2026-05-15'),
  ('MECA_PROFEU_A1',       'Mécanicien protection-incendie',    'Apprenti période 1',  46.33, 'metier', 291, 'seed-2026-05-15'),
  ('MECA_PROFEU_A2',       'Mécanicien protection-incendie',    'Apprenti période 2',  53.71, 'metier', 292, 'seed-2026-05-15'),
  ('MECA_PROFEU_A3',       'Mécanicien protection-incendie',    'Apprenti période 3',  61.08, 'metier', 293, 'seed-2026-05-15'),
  ('MECA_PROFEU_A4',       'Mécanicien protection-incendie',    'Apprenti période 4',  72.16, 'metier', 294, 'seed-2026-05-15'),
  ('MONTEUR_ASS_C',        'Monteur-assembleur',                'Compagnon',           87.41, 'metier', 300, 'seed-2026-05-15'),
  ('MONTEUR_ASS_A1',       'Monteur-assembleur',                'Apprenti période 1',  55.69, 'metier', 301, 'seed-2026-05-15'),
  ('MONTEUR_ASS_A2',       'Monteur-assembleur',                'Apprenti période 2',  63.39, 'metier', 302, 'seed-2026-05-15'),
  ('MONTEUR_ASS_A3',       'Monteur-assembleur',                'Apprenti période 3',  74.95, 'metier', 303, 'seed-2026-05-15'),
  ('VITRIER_C',            'Monteur mécanicien (vitrier)',      'Compagnon',           84.27, 'metier', 310, 'seed-2026-05-15'),
  ('VITRIER_A1',           'Monteur mécanicien (vitrier)',      'Apprenti période 1',  53.60, 'metier', 311, 'seed-2026-05-15'),
  ('VITRIER_A2',           'Monteur mécanicien (vitrier)',      'Apprenti période 2',  61.04, 'metier', 312, 'seed-2026-05-15'),
  ('VITRIER_A3',           'Monteur mécanicien (vitrier)',      'Apprenti période 3',  72.20, 'metier', 313, 'seed-2026-05-15'),
  ('OP_LOURD_AA_C',        'Opérateur d''équipement lourd classe AA', 'Compagnon',     80.89, 'metier', 320, 'seed-2026-05-15'),
  ('OP_LOURD_AA_A',        'Opérateur d''équipement lourd classe AA', 'Apprenti',      69.42, 'metier', 321, 'seed-2026-05-15'),
  ('OP_LOURD_A_C',         'Opérateur d''équipement lourd classe A',  'Compagnon',     78.66, 'metier', 322, 'seed-2026-05-15'),
  ('OP_LOURD_A_A',         'Opérateur d''équipement lourd classe A',  'Apprenti',      67.51, 'metier', 323, 'seed-2026-05-15'),
  ('OP_LOURD_B_C',         'Opérateur d''équipement lourd classe B',  'Compagnon',     76.90, 'metier', 324, 'seed-2026-05-15'),
  ('OP_LOURD_B_A',         'Opérateur d''équipement lourd classe B',  'Apprenti',      66.02, 'metier', 325, 'seed-2026-05-15'),
  ('OP_PELLES_AA_C',       'Opérateur de pelles mécaniques AA', 'Compagnon',           85.70, 'metier', 330, 'seed-2026-05-15'),
  ('OP_PELLES_AA_A',       'Opérateur de pelles mécaniques AA', 'Apprenti',            73.48, 'metier', 331, 'seed-2026-05-15'),
  ('OP_PELLES_A_C',        'Opérateur de pelles mécaniques A',  'Compagnon',           83.37, 'metier', 332, 'seed-2026-05-15'),
  ('OP_PELLES_A_A',        'Opérateur de pelles mécaniques A',  'Apprenti',            71.51, 'metier', 333, 'seed-2026-05-15'),
  ('OP_PELLES_B_C',        'Opérateur de pelles mécaniques B',  'Compagnon',           81.03, 'metier', 334, 'seed-2026-05-15'),
  ('OP_PELLES_B_A',        'Opérateur de pelles mécaniques B',  'Apprenti',            69.52, 'metier', 335, 'seed-2026-05-15'),
  ('OP_PBETON_42M_C',      'Opér. pompe à béton (mât de distribution) -42 M', 'Compagnon', 78.51, 'metier', 340, 'seed-2026-05-15'),
  ('OP_PBETON_42M_A',      'Opér. pompe à béton (mât de distribution) -42 M', 'Apprenti',  67.27, 'metier', 341, 'seed-2026-05-15'),
  ('OP_PBETON_42P_C',      'Opér. pompe à béton (mât de distribution) +42 M', 'Compagnon', 81.51, 'metier', 342, 'seed-2026-05-15'),
  ('OP_PBETON_42P_A',      'Opér. pompe à béton (mât de distribution) +42 M', 'Apprenti',  69.82, 'metier', 343, 'seed-2026-05-15'),
  ('OP_PBETON_50P_C',      'Opér. pompe à béton (mât de distribution) +50 M', 'Compagnon', 83.00, 'metier', 344, 'seed-2026-05-15'),
  ('OP_PBETON_50P_A',      'Opér. pompe à béton (mât de distribution) +50 M', 'Apprenti',  71.07, 'metier', 345, 'seed-2026-05-15'),
  ('OP_PBETON_58P_C',      'Opér. pompe à béton (mât de distribution) +58 M', 'Compagnon', 86.01, 'metier', 346, 'seed-2026-05-15'),
  ('OP_PBETON_58P_A',      'Opér. pompe à béton (mât de distribution) +58 M', 'Apprenti',  73.63, 'metier', 347, 'seed-2026-05-15'),
  ('OP_PBETON_63P_C',      'Opér. pompe à béton (mât de distribution) +63 M', 'Compagnon', 91.10, 'metier', 348, 'seed-2026-05-15'),
  ('OP_PBETON_63P_A',      'Opér. pompe à béton (mât de distribution) +63 M', 'Apprenti',  77.97, 'metier', 349, 'seed-2026-05-15'),
  ('PARQUETEUR_C',         'Parqueteur-sableur',                'Compagnon',           84.64, 'metier', 360, 'seed-2026-05-15'),
  ('PARQUETEUR_A1',        'Parqueteur-sableur',                'Apprenti période 1',  53.87, 'metier', 361, 'seed-2026-05-15'),
  ('PARQUETEUR_A2',        'Parqueteur-sableur',                'Apprenti période 2',  61.33, 'metier', 362, 'seed-2026-05-15'),
  ('PARQUETEUR_A3',        'Parqueteur-sableur',                'Apprenti période 3',  72.54, 'metier', 363, 'seed-2026-05-15'),
  ('PEINTRE_C',            'Peintre',                           'Compagnon',           80.26, 'metier', 370, 'seed-2026-05-15'),
  ('PEINTRE_A1',           'Peintre',                           'Apprenti période 1',  51.14, 'metier', 371, 'seed-2026-05-15'),
  ('PEINTRE_A2',           'Peintre',                           'Apprenti période 2',  58.18, 'metier', 372, 'seed-2026-05-15'),
  ('PEINTRE_A3',           'Peintre',                           'Apprenti période 3',  68.77, 'metier', 373, 'seed-2026-05-15'),
  ('PLATRIER_C',           'Plâtrier',                          'Compagnon',           81.80, 'metier', 380, 'seed-2026-05-15'),
  ('PLATRIER_A1',          'Plâtrier',                          'Apprenti période 1',  52.20, 'metier', 381, 'seed-2026-05-15'),
  ('PLATRIER_A2',          'Plâtrier',                          'Apprenti période 2',  59.36, 'metier', 382, 'seed-2026-05-15'),
  ('PLATRIER_A3',          'Plâtrier',                          'Apprenti période 3',  70.13, 'metier', 383, 'seed-2026-05-15'),
  ('POSEUR_SOUPLES_C',     'Poseur de revêtements souples',     'Compagnon',           82.91, 'metier', 390, 'seed-2026-05-15'),
  ('POSEUR_SOUPLES_A1',    'Poseur de revêtements souples',     'Apprenti période 1',  52.80, 'metier', 391, 'seed-2026-05-15'),
  ('POSEUR_SOUPLES_A2',    'Poseur de revêtements souples',     'Apprenti période 2',  60.10, 'metier', 392, 'seed-2026-05-15'),
  ('POSEUR_SOUPLES_A3',    'Poseur de revêtements souples',     'Apprenti période 3',  71.04, 'metier', 393, 'seed-2026-05-15'),
  ('POSEUR_SYSINT_C',      'Poseur de systèmes intérieurs',     'Compagnon',           85.14, 'metier', 400, 'seed-2026-05-15'),
  ('POSEUR_SYSINT_A1',     'Poseur de systèmes intérieurs',     'Apprenti période 1',  54.37, 'metier', 401, 'seed-2026-05-15'),
  ('POSEUR_SYSINT_A2',     'Poseur de systèmes intérieurs',     'Apprenti période 2',  61.83, 'metier', 402, 'seed-2026-05-15'),
  ('POSEUR_SYSINT_A3',     'Poseur de systèmes intérieurs',     'Apprenti période 3',  73.03, 'metier', 403, 'seed-2026-05-15'),
  ('TIREUR_PL_C',          'Tireur de joints (plâtrier)',       'Compagnon',           80.50, 'metier', 410, 'seed-2026-05-15'),
  ('TIREUR_PL_A1',         'Tireur de joints (plâtrier)',       'Apprenti période 1',  51.39, 'metier', 411, 'seed-2026-05-15'),
  ('TIREUR_PL_A2',         'Tireur de joints (plâtrier)',       'Apprenti période 2',  58.46, 'metier', 412, 'seed-2026-05-15'),
  ('TIREUR_PL_A3',         'Tireur de joints (plâtrier)',       'Apprenti période 3',  69.03, 'metier', 413, 'seed-2026-05-15'),
  ('TIREUR_PE_C',          'Tireur de joints (peintre)',        'Compagnon',           80.45, 'metier', 414, 'seed-2026-05-15'),
  ('TIREUR_PE_A1',         'Tireur de joints (peintre)',        'Apprenti période 1',  51.26, 'metier', 415, 'seed-2026-05-15'),
  ('TIREUR_PE_A2',         'Tireur de joints (peintre)',        'Apprenti période 2',  58.32, 'metier', 416, 'seed-2026-05-15'),
  ('TIREUR_PE_A3',         'Tireur de joints (peintre)',        'Apprenti période 3',  68.92, 'metier', 417, 'seed-2026-05-15'),
  ('TUYAUTEUR_C',          'Tuyauteur',                         'Compagnon',           84.40, 'metier', 420, 'seed-2026-05-15'),
  ('TUYAUTEUR_A1',         'Tuyauteur',                         'Apprenti période 1',  46.60, 'metier', 421, 'seed-2026-05-15'),
  ('TUYAUTEUR_A2',         'Tuyauteur',                         'Apprenti période 2',  53.98, 'metier', 422, 'seed-2026-05-15'),
  ('TUYAUTEUR_A3',         'Tuyauteur',                         'Apprenti période 3',  61.34, 'metier', 423, 'seed-2026-05-15'),
  ('TUYAUTEUR_A4',         'Tuyauteur',                         'Apprenti période 4',  72.41, 'metier', 424, 'seed-2026-05-15'),
  ('CHEF_EQUIPE_CHARP',    'Chef d''équipe (charpentier-menuisier)', 'Chef d''équipe', 90.60, 'metier', 430, 'seed-2026-05-15'),
  ('CHEF_GROUPE_CHARP',    'Chef de groupe (charpentier-menuisier)', 'Chef de groupe', 92.09, 'metier', 431, 'seed-2026-05-15');

-- ─── OCCUPATIONS ──────────────────────────────────────────────────────
INSERT INTO ad_budget.taux_horaires (code, metier, qualification, taux_col17, groupe, ordre, updated_by) VALUES
  ('OCC_CHAUFFEUR_VAPEUR',     'Chauffeur de chaudière à vapeur',               NULL, 69.45, 'occupation', 510, 'seed-2026-05-15'),
  ('OCC_CHAUFFEUR_4',          'Chauffeur classe IV',                           NULL, 62.81, 'occupation', 520, 'seed-2026-05-15'),
  ('OCC_COMMIS',               'Commis',                                        NULL, 51.62, 'occupation', 530, 'seed-2026-05-15'),
  ('OCC_COND_CAMION_AA',       'Conducteur de camion classe AA',                NULL, 73.60, 'occupation', 540, 'seed-2026-05-15'),
  ('OCC_COND_CAMION_A',        'Conducteur de camion classe A',                 NULL, 71.33, 'occupation', 541, 'seed-2026-05-15'),
  ('OCC_COND_CAMION_B',        'Conducteur de camion classe B',                 NULL, 70.26, 'occupation', 542, 'seed-2026-05-15'),
  ('OCC_COND_CAMION_C',        'Conducteur de camion classe C',                 NULL, 69.47, 'occupation', 543, 'seed-2026-05-15'),
  ('OCC_GARDIEN',              'Gardien',                                       NULL, 41.16, 'occupation', 550, 'seed-2026-05-15'),
  ('OCC_HOMME_SERVICE_ML',     'Homme de service sur machines lourdes',         NULL, 67.85, 'occupation', 560, 'seed-2026-05-15'),
  ('OCC_MAGASINIER',           'Magasinier',                                    NULL, 59.51, 'occupation', 570, 'seed-2026-05-15'),
  ('OCC_MANOEUVRE_JOURN',      'Manœuvre (journalier)',                         NULL, 70.05, 'occupation', 580, 'seed-2026-05-15'),
  ('OCC_MANOEUVRE_PIPE',       'Manœuvre (pipeline)',                           NULL, 69.89, 'occupation', 581, 'seed-2026-05-15'),
  ('OCC_MANOEUVRE_COUVERT',    'Manœuvre (travaux de couverture)',              NULL, 71.35, 'occupation', 582, 'seed-2026-05-15'),
  ('OCC_MANOEUVRE_MACO',       'Manœuvre (maçonnerie)',                         NULL, 73.34, 'occupation', 583, 'seed-2026-05-15'),
  ('OCC_MANOEUVRE_DECON',      'Manœuvre en décontamination',                   NULL, 77.55, 'occupation', 584, 'seed-2026-05-15'),
  ('OCC_MANOEUVRE_SPEC',       'Manœuvre spécialisé',                           NULL, 71.02, 'occupation', 585, 'seed-2026-05-15'),
  ('OCC_MANOEUVRE_CARR',       'Manœuvre spécialisé (carreleur)',               NULL, 72.38, 'occupation', 586, 'seed-2026-05-15'),
  ('OCC_MANOEUVRE_COUV',       'Manœuvre spécialisé (couverture)',              NULL, 72.32, 'occupation', 587, 'seed-2026-05-15'),
  ('OCC_LEVAGE_A',             'Opér. d''appareils de levage classe A',         NULL, 75.29, 'occupation', 590, 'seed-2026-05-15'),
  ('OCC_LEVAGE_B',             'Opér. d''appareils de levage classe B',         NULL, 72.59, 'occupation', 591, 'seed-2026-05-15'),
  ('OCC_GENERATRICE',          'Opérateur de génératrice',                      NULL, 71.36, 'occupation', 600, 'seed-2026-05-15'),
  ('OCC_POMPES_COMP',          'Opér. de pompes et compresseurs',               NULL, 72.83, 'occupation', 610, 'seed-2026-05-15'),
  ('OCC_POMPES_LIGNE',         'Opér. de pompes et compr. (pompe à ligne)',     NULL, 72.83, 'occupation', 611, 'seed-2026-05-15'),
  ('OCC_USINES',               'Opérateur d''usines fixes ou mobiles',          NULL, 74.15, 'occupation', 620, 'seed-2026-05-15'),
  ('OCC_PNEUS_DEBOSS',         'Préposé aux pneus et débosselage de machines lourdes', NULL, 75.19, 'occupation', 630, 'seed-2026-05-15'),
  ('OCC_SOUDEUR',              'Soudeur',                                       NULL, 79.05, 'occupation', 640, 'seed-2026-05-15'),
  ('OCC_SOUDEUR_PIPELINE',     'Soudeur de pipeline et de distribution',        NULL, 84.25, 'occupation', 641, 'seed-2026-05-15'),
  ('OCC_SOUDEUR_TUYAU',        'Soudeur en tuyauterie',                         NULL, 84.40, 'occupation', 642, 'seed-2026-05-15'),
  ('OCC_GASFITTER',            'Spécialiste en branchement d''immeubles (gas fitter)', NULL, 83.31, 'occupation', 650, 'seed-2026-05-15');

-- ─── NON-SYNDIQUÉS ────────────────────────────────────────────────────
-- ATTENTION : la grille ACQ ne calcule pas col 17 pour les non-syndiqués
-- (toutes les cellules intermédiaires sont '-'). On utilise la col 20
-- (Total avant frais administration et profit) qui est la seule valeur
-- agrégée disponible. Documenté dans `notes`.
INSERT INTO ad_budget.taux_horaires (code, metier, qualification, taux_col17, groupe, ordre, notes, updated_by) VALUES
  ('NS_AGENT_SST',         'Agent de prévention SST',           NULL, 107.28, 'non_syndique', 710, 'col 20 grille ACQ (col 17 non calculée pour non-syndiqués). Taux salaire brut : 53,64 $/h.', 'seed-2026-05-15'),
  ('NS_ESTIMATEUR',        'Estimateur',                        NULL, 149.55, 'non_syndique', 720, 'col 20 grille ACQ. Taux salaire brut : 65,02 $/h.', 'seed-2026-05-15'),
  ('NS_GERANT_PROJET',     'Gérant de projet',                  NULL, 139.32, 'non_syndique', 730, 'col 20 grille ACQ. Taux salaire brut : 69,66 $/h.', 'seed-2026-05-15'),
  ('NS_INGENIEUR',         'Ingénieur',                         NULL, 150.19, 'non_syndique', 740, 'col 20 grille ACQ. Taux salaire brut : 65,30 $/h.', 'seed-2026-05-15'),
  ('NS_PERS_AUX',          'Personnel auxiliaire',              NULL,  56.10, 'non_syndique', 750, 'col 20 grille ACQ. Taux salaire brut : 24,39 $/h.', 'seed-2026-05-15'),
  ('NS_SURINTENDANT',      'Surintendant de chantier',          NULL, 140.68, 'non_syndique', 760, 'col 20 grille ACQ. Taux salaire brut : 70,34 $/h.', 'seed-2026-05-15'),
  ('NS_TECH_IMMO_JR',      'Technicien en immotique (junior)',  NULL,  91.75, 'non_syndique', 770, 'col 20 grille ACQ. Taux salaire brut : 39,89 $/h.', 'seed-2026-05-15'),
  ('NS_TECH_IMMO',         'Technicien en immotique',           NULL, 109.04, 'non_syndique', 780, 'col 20 grille ACQ. Taux salaire brut : 47,41 $/h.', 'seed-2026-05-15'),
  ('NS_TECH_ADMIN',        'Technicien en administration',      NULL, 100.44, 'non_syndique', 790, 'col 20 grille ACQ. Taux salaire brut : 43,67 $/h.', 'seed-2026-05-15');

-- Vérif intra-transaction : count par groupe
SELECT groupe, COUNT(*) AS n FROM ad_budget.taux_horaires GROUP BY groupe ORDER BY groupe;
-- Attendu :
--   metier         137  (130 normaux + 5 apprentis P4/P5 + 2 chefs)
--   non_syndique     9
--   occupation      29
--   TOTAL          175

SELECT COUNT(*) AS n_total FROM ad_budget.taux_horaires;
-- Attendu : 175

-- ───────────────────────────────────────────────────────────────────
-- Si counts OK -> COMMIT;  sinon -> ROLLBACK;
-- ───────────────────────────────────────────────────────────────────


-- ════════════════════════════════════════════════════════════════════
-- BLOC 3 — INSERT csi_division_default_metier (mapping)
-- ════════════════════════════════════════════════════════════════════
-- À exécuter APRÈS le COMMIT du Bloc 2.

BEGIN;

INSERT INTO ad_budget.csi_division_default_metier (csi_division, taux_id, updated_by) VALUES
  ('01', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('02', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('03', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('04', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('05', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('06', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('07', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('08', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('09', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('10', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('11', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('12', (SELECT id FROM ad_budget.taux_horaires WHERE code='CHARPENTIER_C'),    'seed-2026-05-15'),
  ('14', (SELECT id FROM ad_budget.taux_horaires WHERE code='MECA_ASCENSEUR_C'), 'seed-2026-05-15'),
  ('20', (SELECT id FROM ad_budget.taux_horaires WHERE code='CALORIFUGEUR_C'),   'seed-2026-05-15'),
  ('21', (SELECT id FROM ad_budget.taux_horaires WHERE code='MECA_PROFEU_C'),    'seed-2026-05-15'),
  ('22', (SELECT id FROM ad_budget.taux_horaires WHERE code='TUYAUTEUR_C'),      'seed-2026-05-15'),
  ('23', (SELECT id FROM ad_budget.taux_horaires WHERE code='FERBLANTIER_C'),    'seed-2026-05-15'),
  ('25', (SELECT id FROM ad_budget.taux_horaires WHERE code='ELECTRICIEN_C'),    'seed-2026-05-15'),
  ('26', (SELECT id FROM ad_budget.taux_horaires WHERE code='ELECTRICIEN_C'),    'seed-2026-05-15');

-- Vérif intra-transaction : 19 mappings, aucun NULL taux_id
SELECT COUNT(*) AS n_mappings,
       COUNT(*) FILTER (WHERE taux_id IS NULL) AS n_null
FROM ad_budget.csi_division_default_metier;
-- Attendu : 19 / 0

-- Vérif visuelle : division -> métier
SELECT m.csi_division, t.code, t.metier, t.taux_col17
FROM ad_budget.csi_division_default_metier m
JOIN ad_budget.taux_horaires t ON t.id = m.taux_id
ORDER BY m.csi_division;
-- Attendu : 19 rows, divisions 01..12 toutes -> Charpentier-menuisier 84.64

-- ───────────────────────────────────────────────────────────────────
-- Si counts OK -> COMMIT;  sinon -> ROLLBACK;
-- ───────────────────────────────────────────────────────────────────


-- ════════════════════════════════════════════════════════════════════
-- BLOC 4 — Contrôle APRÈS (post-COMMIT du Bloc 3)
-- ════════════════════════════════════════════════════════════════════

-- 4.1 — Sanity counts
SELECT
  (SELECT COUNT(*) FROM ad_budget.taux_horaires)                 AS n_taux,
  (SELECT COUNT(*) FROM ad_budget.csi_division_default_metier)   AS n_mapping;
-- Attendu : 175 / 19

-- 4.2 — Distribution par groupe
SELECT groupe, COUNT(*) AS n FROM ad_budget.taux_horaires GROUP BY groupe ORDER BY groupe;
-- Attendu : metier 137, non_syndique 9, occupation 29

-- 4.3 — Aucun code dupliqué
SELECT code, COUNT(*) AS n FROM ad_budget.taux_horaires GROUP BY code HAVING COUNT(*) > 1;
-- Attendu : 0 rows

-- 4.4 — Tous les taux > 0
SELECT COUNT(*) FROM ad_budget.taux_horaires WHERE taux_col17 <= 0;
-- Attendu : 0

-- 4.5 — Smoke test de la résolution de défaut pour quelques divisions
SELECT m.csi_division, t.code, t.metier, t.taux_col17
FROM ad_budget.csi_division_default_metier m
JOIN ad_budget.taux_horaires t ON t.id = m.taux_id
WHERE m.csi_division IN ('03','14','22','26')
ORDER BY m.csi_division;
-- Attendu :
--   03  CHARPENTIER_C     Charpentier-menuisier             84.64
--   14  MECA_ASCENSEUR_C  Mécanicien d'ascenseurs           91.93
--   22  TUYAUTEUR_C       Tuyauteur                          84.40
--   26  ELECTRICIEN_C     Électricien                        86.02
