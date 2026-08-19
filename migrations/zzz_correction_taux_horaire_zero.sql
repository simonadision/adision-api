-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — correction retroactive : taux_horaire a 0/NULL avec heures > 0
-- ════════════════════════════════════════════════════════════════════
--
-- Brief pont 2026-08-19 (Simon, capture d'ecran) : sur les lignes de type
-- main-d'oeuvre, TAUX HORAIRE peut rester a 0 $ alors que des heures sont
-- saisies -- le cout main-d'oeuvre (heures x taux) devient silencieusement
-- 0 $, sous-evaluant le vrai cout. Cas rapporte : item "10 28 10.01 --
-- Accessoires de SDB" a 0 $ de taux sur 9 lots du projet HABVA60_85770
-- (Ad BUD, projet id 290) -- propage par duplication de lot (chaque lot
-- copiait taux_horaire=0 tel quel depuis la ligne source).
--
-- REGLE : un taux horaire ne doit JAMAIS rester a 0. Defaut retenu :
-- CHARPENTIER_C (Charpentier-menuisier Compagnon), deja le metier par
-- defaut le plus courant du mapping division CSI (ad_budget.
-- csi_division_default_metier, sprint2_taux_horaires_seed.sql,
-- sprint6_8_csi_division_coverage.sql). Taux courant a l'ecriture de cette
-- migration : 84,64 $/h (col 17, grille ACQ 2026-04-26).
--
-- PORTEE : TOUTES les lignes actives de TOUS les projets (pas seulement
-- HABVA60_85770), ou taux_horaire est 0 ou NULL ET heures > 0.
-- NE TOUCHE JAMAIS une ligne dont le taux est deja non-nul, meme faible.
--
-- IDEMPOTENTE ET REJOUABLE : la CTE `cible` ne retient que les lignes pas
-- deja loggees (NOT EXISTS sur taux_horaire_correction_log) -- un rejeu
-- apres application ne trouve plus rien (le taux corrige n'est plus 0, et
-- la ligne est de toute facon deja loggee).
--
-- TRACABILITE : chaque ligne corrigee est journalisee dans la nouvelle
-- table taux_horaire_correction_log (avant/apres, quand, par quoi) --
-- necessaire si un client questionne plus tard un montant modifie sur un
-- projet deja soumis/gagne.
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_budget.taux_horaire_correction_log (
  id                   SERIAL PRIMARY KEY,
  budget_ligne_id      INTEGER NOT NULL REFERENCES ad_budget.budget_lignes(id) ON DELETE CASCADE,
  projet_id            INTEGER NOT NULL,
  lot_id               INTEGER,
  section              TEXT,
  description          TEXT,
  heures               NUMERIC,
  taux_avant           NUMERIC,
  taux_apres           NUMERIC NOT NULL,
  taux_horaire_source  TEXT NOT NULL DEFAULT 'CHARPENTIER_C',
  corrige_le           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  corrige_par          TEXT NOT NULL DEFAULT 'migration_zzz_correction_taux_horaire_zero'
);

CREATE INDEX IF NOT EXISTS idx_taux_horaire_correction_log_projet
  ON ad_budget.taux_horaire_correction_log(projet_id);

COMMENT ON TABLE ad_budget.taux_horaire_correction_log IS
  'Trace des budget_lignes dont taux_horaire a ete corrige automatiquement '
  'de 0/NULL vers un defaut (brief pont 2026-08-19, Simon) -- tracabilite '
  'si un client questionne un montant modifie retroactivement sur un '
  'projet deja soumis/gagne.';

WITH cible AS (
  SELECT bl.id, bl.projet_id, bl.lot_id, bl.section, bl.description,
         bl.heures, bl.taux_horaire
  FROM ad_budget.budget_lignes bl
  WHERE bl.actif = TRUE
    AND (bl.taux_horaire IS NULL OR bl.taux_horaire = 0)
    AND bl.heures > 0
    AND NOT EXISTS (
      SELECT 1 FROM ad_budget.taux_horaire_correction_log l
      WHERE l.budget_ligne_id = bl.id
    )
),
secours AS (
  SELECT taux_col17 FROM ad_budget.taux_horaires
  WHERE code = 'CHARPENTIER_C' AND actif = TRUE
),
loggees AS (
  INSERT INTO ad_budget.taux_horaire_correction_log
    (budget_ligne_id, projet_id, lot_id, section, description, heures,
     taux_avant, taux_apres)
  SELECT c.id, c.projet_id, c.lot_id, c.section, c.description, c.heures,
         c.taux_horaire, s.taux_col17
  FROM cible c CROSS JOIN secours s
  RETURNING budget_ligne_id, taux_apres
)
UPDATE ad_budget.budget_lignes bl
SET taux_horaire = l.taux_apres,
    updated_at = NOW()
FROM loggees l
WHERE bl.id = l.budget_ligne_id;
