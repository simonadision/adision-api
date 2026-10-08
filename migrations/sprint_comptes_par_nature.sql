-- sprint_comptes_par_nature.sql — PC1, 2026-10-08. Contrat partagé : issue
-- monorepo #1096 ; Ad CON a fait la même chose dans sa migration 038.
--
-- TROIS COMPTES PAR LIGNE, ENREGISTRÉS AVEC LA LIGNE. Simon, 8 oct. 2026 :
--   15 h 35 : « un item doit avoir un compte propre ; une fois la charte
--             associée à un item, c'est enregistré en base avec l'item ; le
--             compte de charte Q est aussi important que le # CSI » ;
--   15 h 54 : « une colonne charte Q pour chacune des sections matériaux,
--             main d'oeuvre, sous-traitant ».
--
-- UNE COLONNE PAR NATURE : compte_mat, compte_mo, compte_st. Le compte unique
-- `compte_metier_force` ne savait pas dire « 55010 pour le matériau, 50005
-- pour la main-d'œuvre » : il fallait deviner à quelle nature il appartenait.
-- Il est CONSERVÉ (lecture, niveau (a) de modules/correspondance_comptes.py),
-- et copié à la duplication, mais les nouvelles routes n'y écrivent plus.
--
-- ⚠ CE FICHIER CONTREDIT UN COMMENTAIRE PLUS ANCIEN, ET IL L'EMPORTE.
-- migrations/sprint_compte_metier.sql (PC3, 30 sept. 2026) dit de
-- `compte_metier_force` : « NULL = suis la correspondance », « NE JAMAIS y
-- recopier le compte déduit ». La décision de Simon du 8 oct. (15 h 35,
-- citée plus haut) est POSTÉRIEURE et dit l'inverse : le compte trouvé par la
-- charte est ENREGISTRÉ sur la ligne. C'est le 8 oct. qui fait foi. Le vieux
-- commentaire reste vrai pour SA colonne (qu'on n'écrit plus), il ne
-- s'applique pas aux trois nouvelles.
--
-- LE COÛT DE CETTE DÉCISION, ÉCRIT POUR QU'IL NE SURPRENNE PERSONNE :
--   le jour où une paire de la correspondance (code CSI, nature) -> compte
--   est CORRIGÉE dans la charte Q, les lignes DÉJÀ enregistrées NE SUIVENT
--   PAS. Elles gardent l'ancien compte, et il faut les revoir UNE PAR UNE
--   (le journal ci-dessous dit lesquelles ont été posées « depuis la charte »).
--   C'est exactement le risque que l'ancien commentaire voulait éviter.
-- LA CONTREPARTIE, voulue par Simon : chaque item porte un compte PROPRE,
--   stable, aussi important que son # CSI ; une correction de la charte ne
--   change plus en silence la comptabilité d'un budget déjà monté, et un
--   compte posé à la main n'est jamais écrasé par la charte.
--
-- HORS TOTAUX ET HORS EMPREINTE, comme compte_metier_force : un numéro de
-- compte n'est pas un montant (tests/test_budget_fingerprint_champs_neutres.py).
-- TEXT et non entier : zéros de tête (« 04010 »), identifiant, pas quantité.
-- PAS DE CLÉ ÉTRANGÈRE : la charte vit au hub, dans une autre base.

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS compte_mat TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS compte_mo  TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS compte_st  TEXT DEFAULT NULL;

-- Même garde que compte_metier_force, même raison : la chaîne vide et les
-- blancs ne sont pas des comptes. L'absence de compte s'écrit NULL. NOT VALID :
-- s'applique aux écritures futures sans balayer la table au démarrage.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'budget_lignes_compte_mat_non_vide') THEN
    ALTER TABLE ad_budget.budget_lignes
      ADD CONSTRAINT budget_lignes_compte_mat_non_vide
      CHECK (compte_mat IS NULL OR btrim(compte_mat) <> '') NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'budget_lignes_compte_mo_non_vide') THEN
    ALTER TABLE ad_budget.budget_lignes
      ADD CONSTRAINT budget_lignes_compte_mo_non_vide
      CHECK (compte_mo IS NULL OR btrim(compte_mo) <> '') NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'budget_lignes_compte_st_non_vide') THEN
    ALTER TABLE ad_budget.budget_lignes
      ADD CONSTRAINT budget_lignes_compte_st_non_vide
      CHECK (compte_st IS NULL OR btrim(compte_st) <> '') NOT VALID;
  END IF;
END $$;

COMMENT ON COLUMN ad_budget.budget_lignes.compte_mat IS
  'PC1 2026-10-08 — compte charte Q ENREGISTRE pour les MATERIAUX de la ligne '
  '(Simon 8 oct. 15 h 35). Gagne sur tout le reste de la resolution. '
  'HORS TOTAUX et HORS EMPREINTE.';
COMMENT ON COLUMN ad_budget.budget_lignes.compte_mo IS
  'PC1 2026-10-08 — compte charte Q ENREGISTRE pour la MAIN-D''OEUVRE de la ligne. '
  'HORS TOTAUX et HORS EMPREINTE.';
COMMENT ON COLUMN ad_budget.budget_lignes.compte_st IS
  'PC1 2026-10-08 — compte charte Q ENREGISTRE pour la SOUS-TRAITANCE de la ligne. '
  'HORS TOTAUX et HORS EMPREINTE.';

-- LE JOURNAL. Un compte « aussi important que le # CSI » ne change pas sans
-- trace : qui, quand, de quoi à quoi, et pourquoi (`note`). C'est lui qui
-- permet de retrouver, le jour où une paire de la charte est corrigée, les
-- lignes posées « depuis la charte Q » qu'il faut revoir une par une.
-- `par` = courriel de l'auteur (la seule clé d'utilisateur commune au hub et
-- à Ad BUD — cf. la détention, 22 juil. 2026).
CREATE TABLE IF NOT EXISTS ad_budget.budget_ligne_compte_journal (
  id           BIGSERIAL PRIMARY KEY,
  -- INTEGER : le type de budget_lignes.id et de projets.id (lu le 8 oct.).
  ligne_id     INTEGER NOT NULL
                 REFERENCES ad_budget.budget_lignes(id) ON DELETE CASCADE,
  projet_id    INTEGER NOT NULL,
  nature       TEXT NOT NULL CHECK (nature IN ('MAT', 'MO', 'ST')),
  compte_avant TEXT,
  compte_apres TEXT,
  par          TEXT,
  le           TIMESTAMPTZ NOT NULL DEFAULT now(),
  note         TEXT
);

CREATE INDEX IF NOT EXISTS budget_ligne_compte_journal_ligne_idx
  ON ad_budget.budget_ligne_compte_journal (ligne_id);
CREATE INDEX IF NOT EXISTS budget_ligne_compte_journal_projet_idx
  ON ad_budget.budget_ligne_compte_journal (projet_id);

-- REPORT DE L'ANCIEN COMPTE UNIQUE — LA VERSION SIMPLE, ET POURQUOI.
-- Le contrat (#1096) dit « nature unique -> sa colonne, sinon MAT ». Savoir
-- quelles natures une ligne PORTE demande ses montants, et le montant de
-- main-d'œuvre passe par la règle des heures effectives (unité « hr »,
-- production_valeur prioritaire) : la réécrire ici en SQL ferait une COPIE de
-- modules/aggregates.heures_effectives, précisément ce que
-- tools/verifier_source_unique.py existe pour empêcher (côté Python).
-- On reporte donc TOUT au MATÉRIAU, comme le défaut de la 038 d'Ad CON, et
-- on ne perd rien : tant que compte_metier_force reste en place, le niveau
-- (a) de la résolution (modules/correspondance_comptes.py) continue de le
-- donner à la nature UNIQUE d'une ligne à nature unique, et la route
-- POST .../comptes/enregistrer l'écrit ensuite dans la BONNE colonne.
-- Effet de bord assumé : une ligne sans matériau peut garder un compte_mat
-- pour une nature absente ; il n'est servi nulle part (seules les natures
-- présentes le sont).
-- Portée mesurée le 8 oct. 2026 (base Ad BUD lue par PC1) : 0 ligne sur
-- 4 939 porte un compte_metier_force — ce report ne touche rien aujourd'hui.
-- IDEMPOTENT : seulement là où les trois nouvelles colonnes sont vides.
UPDATE ad_budget.budget_lignes
   SET compte_mat = compte_metier_force
 WHERE compte_metier_force IS NOT NULL
   AND btrim(compte_metier_force) <> ''
   AND compte_mat IS NULL AND compte_mo IS NULL AND compte_st IS NULL;
