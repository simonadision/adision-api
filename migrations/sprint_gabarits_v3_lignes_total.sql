-- ════════════════════════════════════════════════════════════════════
-- Sprint Gabarits v3 — la ligne TOTAL comme ligne ordinaire du gabarit
--
-- Avant : les regroupements de total vivaient dans une zone à part (jamais
-- persistée côté serveur — la colonne n'a jamais existé, PR #66 du monorepo
-- envoyait un champ `regroupements` que create_gabarit/update_gabarit
-- ignoraient silencieusement).
--
-- Demande Simon, 31 août 2026 : le total est un TYPE DE LIGNE ORDINAIRE
-- dans la MÊME liste que les divisions — titre éditable, déplaçable par
-- glisser-déposer n'importe où dans le gabarit, avec sélection des
-- divisions qu'elle additionne. D'où : deux colonnes de plus sur
-- `gabarit_sections` (même table, même ordre `ordre`, même glisser-déposer
-- que les divisions) plutôt qu'une table séparée.
--
-- `type='total'` : `numero` reste NULL (ce n'est pas un code CSI),
-- `nom_section` porte le TITRE édité par Simon, `total_membres` porte les
-- codes CSI (chaîne, tels que saisis sur les divisions) des divisions que
-- ce total additionne. Aucune valeur ici non plus — le sous-total réel se
-- calcule dans Ad BUD, à partir des lignes du budget qui en naîtra.
--
-- Idempotente (ADD COLUMN IF NOT EXISTS ; contrainte posée seulement si
-- absente, gardée par un SELECT sur pg_constraint).
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.gabarit_sections
  ADD COLUMN IF NOT EXISTS type VARCHAR(20) NOT NULL DEFAULT 'division';

ALTER TABLE ad_budget.gabarit_sections
  ADD COLUMN IF NOT EXISTS total_membres JSONB;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'gabarit_sections_type_check'
  ) THEN
    ALTER TABLE ad_budget.gabarit_sections
      ADD CONSTRAINT gabarit_sections_type_check
      CHECK (type IN ('division', 'total'));
  END IF;
END $$;
