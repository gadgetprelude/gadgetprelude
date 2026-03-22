from sqlalchemy import create_engine, text

# 🔴 IMPORTANTE: cola aqui a DATABASE_URL do Render
DATABASE_URL = "postgresql://gadgetprelude:vhVJZHdXsfgbHI2yKPc9vJ529A3qL6gE@dpg-d6uofipr0fns73br470g-a.oregon-postgres.render.com/gadgetprelude"

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # 1. Criar tabelas
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS providers (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id),
            name VARCHAR NOT NULL
        );
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS provider_services (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id),
            provider_id INTEGER NOT NULL REFERENCES providers(id),
            service_id INTEGER NOT NULL REFERENCES services(id)
        );
    """))

    # 2. Alterar appointments
    conn.execute(text("""
        ALTER TABLE appointments
        ADD COLUMN IF NOT EXISTS provider_id INTEGER REFERENCES providers(id);
    """))

    # 3. Criar providers (Barbearia)
    conn.execute(text("""
        INSERT INTO providers (tenant_id, name)
        VALUES
          ((SELECT id FROM tenants WHERE key = 'default'), 'Barbeiro 1'),
          ((SELECT id FROM tenants WHERE key = 'default'), 'Barbeiro 2')
        ON CONFLICT DO NOTHING;
    """))

    # 4. Associar serviços aos providers
    conn.execute(text("""
        INSERT INTO provider_services (tenant_id, provider_id, service_id)
        SELECT
            t.id,
            p.id,
            s.id
        FROM tenants t
        JOIN providers p ON p.tenant_id = t.id
        JOIN services s ON s.tenant_id = t.id
        WHERE t.key = 'default'
        ON CONFLICT DO NOTHING;
    """))

    conn.commit()

print("PROVIDERS CLOUD DONE")