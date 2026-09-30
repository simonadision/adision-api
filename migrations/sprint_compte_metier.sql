-- sprint_compte_metier.sql — PC3, 2026-09-30, demande de Simon (« go migration »).
--
-- POURQUOI : le pont QuickBooks. Chaque ligne de budget doit pouvoir dire a
-- quel COMPTE COMPTABLE son metier correspond, pour qu'Ad BUD et la comptabilite
-- de Contracta parlent le meme langage, et que le reel revienne par compte.
--
-- CE QUE CETTE COLONNE EST : une CORRECTION MANUELLE, pas le compte de la ligne.
-- Le compte normal se DEDUIT de la section par une table de correspondance
-- propre a chaque organisation. Cette colonne ne sert qu'a dire « pour CETTE
-- ligne-ci, la correspondance se trompe, c'est ce compte-la ». NULL est donc
-- le cas de l'immense majorite des lignes, et il veut dire « suis la
-- correspondance », jamais « pas de compte ».
--
-- CE QU'IL NE FAUT SURTOUT PAS EN FAIRE : y recopier le compte deduit, meme
-- « pour aller plus vite ». Une valeur qui duplique un calcul devient fausse
-- le jour ou le calcul change, et plus rien ne dit laquelle des deux a raison.
-- Si la colonne se met a etre remplie partout, c'est que quelqu'un a confondu
-- une correction avec une valeur.
--
-- HORS TOTAUX ET HORS EMPREINTE, comme format_item et pour la meme raison,
-- mais celle-ci merite d'etre dite en toutes lettres : UN NUMERO DE COMPTE
-- N'EST PAS UN MONTANT. `sous_total`, `total` et l'empreinte se calculent
-- sans lui. Un banc le verrouille (tests/test_budget_fingerprint_champs_neutres.py)
-- et tombera en ROUGE si quelqu'un l'ajoute a un calcul.
--
-- TYPE : TEXT, et c'est deliberement PAS un entier. Un numero de compte est un
-- IDENTIFIANT, pas une quantite : l'additionner, le moyenner ou l'incrementer
-- est toujours un bogue. D'autres chartes portent des zeros de tete (« 04010 »)
-- qu'un entier mangerait, et la charte de Contracta contient deja deux comptes
-- SANS numero -- preuve qu'on ne peut rien supposer de la forme.
--
-- PAS DE CLE ETRANGERE : la charte des comptes vit dans une AUTRE base
-- (app_hub.code_referentiels, kind='compte_qbo', par organisation). Une cle
-- etrangere entre deux bases n'existe pas en PostgreSQL. La coherence est donc
-- verifiee a l'ECRITURE par l'API, et ce qui suit ne garantit que la forme.

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS compte_metier_force TEXT DEFAULT NULL;

-- Contrainte posee separement et NOT VALID : elle s'applique aux ecritures
-- futures sans imposer un balayage de toute la table au demarrage. Les lignes
-- existantes sont toutes NULL (la colonne vient de naitre), donc il n'y a rien
-- a valider -- mais la forme reste bonne si la migration est rejouee sur une
-- base ou des valeurs existent deja.
--
-- CE QU'ELLE REFUSE : la chaine vide et les blancs. Sans elle, '' et '   '
-- passeraient pour des comptes, et une ligne « corrigee vers rien » serait
-- indiscernable d'une ligne qui suit la correspondance -- alors que les deux
-- ne veulent PAS dire la meme chose. L'absence de correction s'ecrit NULL.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'budget_lignes_compte_metier_force_non_vide'
  ) THEN
    ALTER TABLE ad_budget.budget_lignes
      ADD CONSTRAINT budget_lignes_compte_metier_force_non_vide
      CHECK (compte_metier_force IS NULL OR btrim(compte_metier_force) <> '')
      NOT VALID;
  END IF;
END $$;

COMMENT ON COLUMN ad_budget.budget_lignes.compte_metier_force IS
  'PC3 2026-09-30 — compte comptable FORCE pour cette ligne (pont QuickBooks). '
  'NULL = suis la correspondance section -> compte de l''organisation, et c''est '
  'le cas normal. Ne JAMAIS y recopier le compte deduit : la valeur deviendrait '
  'fausse des que la correspondance change. '
  'TEXT et non entier : un numero de compte est un identifiant, pas une quantite. '
  'HORS TOTAUX et HORS EMPREINTE, par decision explicite — un numero de compte '
  'n''est pas un montant.';
