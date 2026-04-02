from db import SessionLocal
from models import AdminUser
from admin_security import hash_password
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

#Para correr em prod ini
DATABASE_URL = "postgresql://gadgetprelude:vhVJZHdXsfgbHI2yKPc9vJ529A3qL6gE@dpg-d6uofipr0fns73br470g-a.oregon-postgres.render.com/gadgetprelude";
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


db = SessionLocal()

email = "cliente.demo@gadgetprelude.com"
password = "Cliente123!"

existing = db.query(AdminUser).filter(AdminUser.email == email).first()

if existing:
    print("User already exists")
else:
    user = AdminUser(
        email=email,
        password_hash=hash_password(password),
        is_active=True,
        is_superuser=False
    )
    db.add(user)
    db.commit()
    print("Tenant user created")