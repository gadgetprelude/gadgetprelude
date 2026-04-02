from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db import SessionLocal 
from models import AdminUser
from admin_security import hash_password

#Para correr em prod. em dev comenta-se
DATABASE_URL = "postgresql://gadgetprelude:vhVJZHdXsfgbHI2yKPc9vJ529A3qL6gE@dpg-d6uofipr0fns73br470g-a.oregon-postgres.render.com/gadgetprelude";
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


db = SessionLocal()

email = "admin@gadgetprelude.com"
password = "Admin123!"

existing = db.query(AdminUser).filter(AdminUser.email == email).first()

if existing:
    print("User already exists")
else:
    user = AdminUser(
        email=email,
        password_hash=hash_password(password),
        is_active=True,
        is_superuser=True
    )
    db.add(user)
    db.commit()
    print("Superuser created")