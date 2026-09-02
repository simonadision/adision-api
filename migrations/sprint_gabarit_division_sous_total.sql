-- Ad BUD — Éditeur de gabarit : marquage MANUEL d'une division comme
-- sous-total (2 sept 2026).
--
-- Simon, en direct, gabarit "ACQ-2e étage" -- deux divisions qui SONT des
-- sous-totaux de son bordereau ("09", "20") sans relation de code fiable
-- entre elles (la tentative précédente déduisait le lien par préfixe
-- numérique commun -- fonctionne pour "09"/"09.1"/"09.4.1", pas pour "20"
-- au-dessus de 22/23/26/27, qui n'ont aucun lien de préfixe) :
--
-- "Je veux un bouton: Sous-total qui identifie cette ligne comme etant un
-- sous total: activer ca devient vert. Donc toutes les lignes ont ce
-- bouton et si je decide de faire un sous total ca devient vert."
--
-- Remplace la déduction automatique (fragile) par un DRAPEAU EXPLICITE,
-- posé à la main par Simon sur N'IMPORTE QUELLE division -- la vérité
-- n'est plus déduite, elle est déclarée.
--
-- Migration non destructive : ADD COLUMN nullable-par-défaut, DEFAULT
-- FALSE (aucune division existante ne devient sous-total toute seule).
-- Tracké ad_budget.schema_migrations (joué une fois). IF NOT EXISTS par
-- sécurité.

ALTER TABLE ad_budget.gabarit_sections
  ADD COLUMN IF NOT EXISTS est_sous_total BOOLEAN NOT NULL DEFAULT FALSE;
