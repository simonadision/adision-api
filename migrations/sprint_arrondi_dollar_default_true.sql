-- Ad BUD — DEFAULT arrondi_dollar = TRUE pour les nouveaux projets (juin 2026).
--
-- Décision : tous les NOUVEAUX projets démarrent en « Arrondi au dollar »
-- (mode 99% des cas en pratique). Les projets EXISTANTS gardent leur réglage
-- actuel — aucune valeur persistée n'est touchée (ALTER ... SET DEFAULT
-- n'affecte que les futurs INSERT sans valeur explicite).
--
-- Anti-régression devis émis : on NE FAIT PAS d'UPDATE de masse sur les
-- lignes existantes. Un projet déjà émis avec ses chiffres en décimales
-- garde EXACTEMENT ses chiffres ; bascule manuelle possible via la modale
-- « ⚙ Paramètres financiers ».
--
-- Cas COPIE / RÉVISION d'un projet existant : le backend lit explicitement
-- arrondi_dollar du parent dans le SELECT (cf. patch ad_budget_api.py
-- même PR), donc la copie/révision PRÉSERVE le réglage parent — JAMAIS
-- ne tombe sur ce nouveau défaut. C'est seulement les vraies créations
-- ex nihilo (POST /budget/projets, from-viu) qui prennent TRUE par défaut.
--
-- Idempotent (SET DEFAULT = no-op si déjà à TRUE). Joué une seule fois
-- via schema_migrations.

ALTER TABLE ad_budget.projets
  ALTER COLUMN arrondi_dollar SET DEFAULT TRUE;
