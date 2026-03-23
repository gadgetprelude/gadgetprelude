from sqlalchemy import create_engine, text

# 🔴 IMPORTANTE: cola aqui a DATABASE_URL do Render
DATABASE_URL = "postgresql://gadgetprelude:vhVJZHdXsfgbHI2yKPc9vJ529A3qL6gE@dpg-d6uofipr0fns73br470g-a.oregon-postgres.render.com/gadgetprelude"

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    conn.execute(text("""
        ALTER TABLE providers
        ADD COLUMN IF NOT EXISTS calendar_email VARCHAR;
    """))
    conn.commit()

print("PROVIDER CALENDAR CLOUD DONE")