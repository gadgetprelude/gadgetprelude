from db import SessionLocal
from models import AdminUser
from admin_security import hash_password

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