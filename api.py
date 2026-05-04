from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from modules.ad_budget_api import register_ad_budget_routes
import os

app = FastAPI(title="Adision API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:6268605Ss@localhost:5432/Adision")

engine = create_engine(DATABASE_URL)

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


app.include_router(register_ad_budget_routes(get_conn))


@app.get("/")
def home():
    return {"message": "API Adision connectée"}


@app.get("/items")
def get_items():
    query = """
        SELECT *
        FROM vue_items_complets
        ORDER BY code;
    """
    df = pd.read_sql_query(query, engine)
    df = df.replace({float("nan"): None})
    df = df.fillna("")
    return df.to_dict(orient="records")


@app.get("/items/search")
def search_items(q: str = ""):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, description
        FROM items
        WHERE actif = true
          AND description ILIKE %s
        ORDER BY description
        LIMIT 50;
    """, (f"%{q}%",))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


@app.get("/mapping/suggestions")
def get_suggestions():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT 
            s.id,
            s.prix_web_item_id,
            s.item_id,
            p.nom_produit,
            p.prix,
            COALESCE(p.url_produit, '') AS url_produit,
            i.description AS item_adision,
            s.score,
            s.statut,
            s.created_at
        FROM suggestions_mapping_items s
        JOIN prix_web_items p ON p.id = s.prix_web_item_id
        JOIN items i ON i.id = s.item_id
        WHERE s.statut = 'a_valider'
        ORDER BY s.id DESC;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


@app.post("/mapping/suggestions/{suggestion_id}/set-item/{item_id}")
def set_suggestion_item(suggestion_id: int, item_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE suggestions_mapping_items
        SET item_id = %s
        WHERE id = %s;
    """, (item_id, suggestion_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "updated"}


@app.post("/mapping/suggestions/{suggestion_id}/accept")
def accept_suggestion(suggestion_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT prix_web_item_id, item_id
        FROM suggestions_mapping_items
        WHERE id = %s;
    """, (suggestion_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": "Suggestion introuvable"}
    prix_web_item_id, item_id = row
    cur.execute("""
        UPDATE suggestions_mapping_items
        SET statut = 'accepte'
        WHERE id = %s;
    """, (suggestion_id,))
    cur.execute("""
        UPDATE prix_web_items
        SET item_id = %s, statut = 'valide'
        WHERE id = %s;
    """, (item_id, prix_web_item_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "accepted"}


@app.post("/mapping/suggestions/{suggestion_id}/accept-learn")
def accept_and_learn(suggestion_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.prix_web_item_id, s.item_id, p.nom_produit
        FROM suggestions_mapping_items s
        JOIN prix_web_items p ON p.id = s.prix_web_item_id
        WHERE s.id = %s;
    """, (suggestion_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": "Suggestion introuvable"}
    prix_web_item_id, item_id, nom_produit = row
    cur.execute("""
        UPDATE suggestions_mapping_items SET statut = 'accepte' WHERE id = %s;
    """, (suggestion_id,))
    cur.execute("""
        UPDATE prix_web_items SET item_id = %s, statut = 'valide' WHERE id = %s;
    """, (item_id, prix_web_item_id))
    cur.execute("""
        INSERT INTO apprentissage_mapping_items (nom_produit_web, item_id, source)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING;
    """, (nom_produit, item_id, "validation_ui"))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "accepted_and_learned"}


@app.post("/mapping/suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE suggestions_mapping_items SET statut = 'rejete' WHERE id = %s;
    """, (suggestion_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "rejected"}
