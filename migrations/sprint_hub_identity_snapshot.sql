-- Brief 1 — MIROIR LOCAL de l'identité projet/client (anti fail-closed PDF/devis).
--
-- La génération du devis officiel et du rapport de calcul (Reportlab, serveur)
-- résolvait l'identité projet/client via le hub en mode FAIL-CLOSED : hub
-- injoignable → 502 → aucun PDF (point de défaillance unique sur le livrable
-- contractuel, même clients parfaitement connectés).
--
-- On stocke désormais un SNAPSHOT local de l'identité (nom, client, no projet,
-- zone/région, contacts, logo_url…) — même patron que le miroir de verrou
-- `is_verrouille`. Le hub reste la SOURCE UNIQUE ; ce snapshot sert de REPLI à
-- la génération quand le hub est indisponible. Rafraîchi paresseusement à chaque
-- GET projet réussi (réconciliation), et à chaque génération quand le hub répond.
--
-- Miroir SQL du bootstrap idempotent api.py `_startup_migrations` (convention
-- adision-api : ADD COLUMN IF NOT EXISTS au démarrage).

ALTER TABLE ad_budget.projets
  ADD COLUMN IF NOT EXISTS hub_identity_snapshot JSONB,
  ADD COLUMN IF NOT EXISTS hub_identity_snapshot_at TIMESTAMPTZ;
