from sqlalchemy import create_engine, text

# 🔴 IMPORTANTE: cola aqui a DATABASE_URL do Render
DATABASE_URL = "postgresql://gadgetprelude:vhVJZHdXsfgbHI2yKPc9vJ529A3qL6gE@dpg-d6uofipr0fns73br470g-a.oregon-postgres.render.com/gadgetprelude"

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:

    # 1. Criar tenant
    conn.execute(text("""
        INSERT INTO tenants (key, name)
        VALUES ('barbearia2840', 'Barbearia 2840')
        ON CONFLICT (key) DO NOTHING;
    """))

    # 2. Criar providers
    conn.execute(text("""
        INSERT INTO providers (tenant_id, name)
        VALUES
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Ricardo Fernandes'),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Will');
    """))

    # 3. Criar serviços
    conn.execute(text("""
        INSERT INTO services (tenant_id, name, duration_minutes)
        VALUES
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Corte', 30),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Barba', 30),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Sobrancelha a navalha', 30),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Corte + Barba', 60),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Corte + pigmentado com microfibra capilar', 30),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Corte + pigmentado com tinta', 30),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Alinhamento sem disfarce', 30),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Madeixas', 30),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Limpeza de pele', 30),
        ((SELECT id FROM tenants WHERE key='barbearia2840'), 'Black Mask', 30);
    """))

    # 4. Associar todos os serviços aos providers
    conn.execute(text("""
        INSERT INTO provider_services (tenant_id, provider_id, service_id)
        SELECT t.id, p.id, s.id
        FROM tenants t
        JOIN providers p ON p.tenant_id = t.id
        JOIN services s ON s.tenant_id = t.id
        WHERE t.key = 'barbearia2840';
    """))

    conn.commit()

print("ONBOARDING BASE DONE")