-- Ad BUD — LOTS, workflow avance (Phase C) : badge "A completer".
--
-- Posee UNIQUEMENT a la creation d'une ligne copiee vers d'autres lots SANS
-- copier la quantite (cf. POST /projets/{id}/lignes, param a_completer).
-- Jamais posee ailleurs, jamais effacee explicitement : la ligne "sort" du
-- badge/rappel des qu'une quantite non nulle est saisie -- c'est un calcul
-- dynamique (a_completer=TRUE ET qte vide/0), pas un etat qu'il faut penser
-- a nettoyer. Cf. modules/lots_calc.py::compute_lot_totals (cle "a_completer"
-- du resultat, reutilisee par GET /lots, l'export Excel et le PDF "par lot").
--
-- Trackee schema_migrations (jouee une fois). IF NOT EXISTS par securite.

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS a_completer BOOLEAN NOT NULL DEFAULT FALSE;
