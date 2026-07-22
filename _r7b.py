import os, psycopg
from psycopg.rows import dict_row
DROP19 = ["nom","nom_client","client","type_batiment","region","date_adjudication","superficie_m2","date_debut","date_fin","adresse","description","numero_projet","contact_client","email_client","telephone_client","contact_entrepreneur","email_entrepreneur","telephone_entrepreneur","logo_base64"]
bud = psycopg.connect(os.environ["BUD_DB"], row_factory=dict_row); bud.autocommit=True; bc=bud.cursor()
bc.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='ad_budget' AND table_name='projets' AND column_name = ANY(%s)", (DROP19,)); print("19 colonnes identite restantes:", bc.fetchone()["count"])
bc.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='ad_budget' AND table_name='projets' AND column_name IN ('statut','client_id','ad_hub_project_id','notes','pct_admin_conditions','revision_label')"); print("propres conservees:", sorted(r['column_name'] for r in bc.fetchall()))
bc.execute("SELECT COUNT(*) AS n FROM ad_budget.projets WHERE organization_id IN (SELECT id FROM ad_budget.projets p2 WHERE FALSE)"); 
bc.execute("SELECT COUNT(*) AS n FROM ad_budget.users WHERE email LIKE 'p7b-%@test.fake'"); print("users jetables residuels:", bc.fetchone()["n"])
bc.execute("SELECT COUNT(*) AS n FROM ad_budget.projets"); print("total projets prod (7 attendu):", bc.fetchone()["n"])
bud.close()
