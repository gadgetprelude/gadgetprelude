from sqlalchemy import create_engine, text
import os

DATABASE_URL = "postgresql://gadgetprelude:vhVJZHdXsfgbHI2yKPc9vJ529A3qL6gE@dpg-d6uofipr0fns73br470g-a.oregon-postgres.render.com/gadgetprelude"

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    conn.execute(text("""
        INSERT INTO tenants (key, name)
        VALUES ('default', 'Gadget Prelude')
        ON CONFLICT (key) DO NOTHING;
    """))

    conn.execute(text("""
        INSERT INTO services (tenant_id, name, duration_minutes)
        VALUES (
          (SELECT id FROM tenants WHERE key = 'default'),
          'Consulta',
          60
        );
    """))

    conn.commit()

print("DONE")