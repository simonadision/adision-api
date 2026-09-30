-- sprint_format_item.sql — PC3, 2026-09-29, demande de PC4 relayee par Simon.
--
-- POURQUOI : Ad BUD doit pouvoir dire dans quel FORMAT un item est achete
-- (la boite de 12, le paquet de 50), independamment de la quantite saisie.
-- L'interface est faite par PC4 ; cette migration ne fait qu'ouvrir le champ.
--
-- CE QUE CE CHAMP N'EST PAS, et c'est la contrainte posee par PC4 :
--   * il N'ENTRE PAS dans les totaux. `sous_total` et `total` sont calcules
--     sans lui. Une ligne qui gagne un format ne change pas d'un cent.
--   * il N'ENTRE PAS dans l'empreinte de la ligne. Le modifier ne doit donc
--     pas faire bouger les empreintes de controle.
-- Si un jour quelqu'un l'ajoute a un calcul de total, qu'il sache qu'il
-- contredit une decision explicite et non un oubli.
--
-- TYPE : NUMERIC(12,4), EXACTEMENT celui de production_valeur, qui est le
-- champ le plus proche par nature. Choisi par PC4 ; je le prends sur le sien
-- plutot que sur le mien (numeric non borne) : borner est mieux, et deux
-- champs voisins doivent se ressembler.
-- NULLABLE : une ligne sans format est le cas NORMAL, pas une anomalie --
-- l'immense majorite des lignes existantes n'en aura jamais.
-- CONTRAINTE > 0 : un format de zero ou negatif n'a pas de sens ; on refuse
-- l'ecriture plutot que de laisser une valeur qui ne veut rien dire. NULL
-- reste permis, c'est l'absence de format.

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS format_item NUMERIC(12,4) DEFAULT NULL;

-- Contrainte posee separement et NOT VALID : elle s'applique aux ecritures
-- futures sans imposer un balayage de toute la table au demarrage. Les
-- lignes existantes sont toutes NULL (la colonne vient de naitre), donc
-- rien a valider -- mais la forme reste bonne si la migration est rejouee
-- sur une base ou des valeurs existent deja.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'budget_lignes_format_item_positif'
  ) THEN
    ALTER TABLE ad_budget.budget_lignes
      ADD CONSTRAINT budget_lignes_format_item_positif
      CHECK (format_item IS NULL OR format_item > 0) NOT VALID;
  END IF;
END $$;

COMMENT ON COLUMN ad_budget.budget_lignes.format_item IS
  'PC3 2026-09-29 — format d''achat de l''item (ex. la boite de 12). '
  'NULL = pas de format, cas normal. Toujours > 0 si renseigne. '
  'HORS TOTAUX et HORS EMPREINTE, par decision explicite : ne pas l''ajouter '
  'a sous_total, total, ni a aucun calcul d''empreinte de ligne.';
