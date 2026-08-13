-- Ad BUD — LOTS (regroupement physique de lignes budgétaires, ex. "AB DEL",
-- "CD DEL", "CS H"...). Structure a 2 niveaux : Projet > Lot > Lignes (pas de
-- sous-niveau par logement individuel -- la quantite d'une ligne dans un lot
-- represente deja qte/logement x nb de logements du lot).
--
-- lot_id sur budget_lignes est NULLABLE : un projet SANS lot continue de
-- fonctionner exactement comme avant (lignes "hors-lot"), aucune regression.
--
-- ON DELETE SET NULL sur lot_id : supprimer un lot ne supprime JAMAIS de
-- lignes budgetaires -- les lignes redeviennent "hors-lot". La suppression
-- de lignes reste un choix explicite separe.
--
-- Trackee schema_migrations (jouee une fois). IF NOT EXISTS par securite.

CREATE TABLE IF NOT EXISTS ad_budget.lots (
  id           SERIAL PRIMARY KEY,
  projet_id    INTEGER NOT NULL REFERENCES ad_budget.projets(id) ON DELETE CASCADE,
  nom          TEXT NOT NULL,
  nb_logements INTEGER,
  ordre        INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lots_projet_id ON ad_budget.lots (projet_id);

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS lot_id INTEGER REFERENCES ad_budget.lots(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_budget_lignes_lot_id ON ad_budget.budget_lignes (lot_id);
