-- Ad BUD — Paramètres financiers : option d'arrondissement au dollar.
--
-- Flag par projet. FALSE (défaut) = mode actuel « avec décimales » (cents),
-- rétrocompat totale. TRUE = mode « arrondi au dollar » : totaux de ligne,
-- A&P par division, sous-total, TPS, TVQ arrondis au dollar le plus près.
-- C'est un mode de CALCUL/AFFICHAGE — les coûts saisis restent au cent près
-- en base (réversible : repasser en décimales redonne les valeurs exactes).
--
-- Tracké schema_migrations (joué une fois). ADD COLUMN IF NOT EXISTS = idempotent.

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS arrondi_dollar BOOLEAN NOT NULL DEFAULT FALSE;
