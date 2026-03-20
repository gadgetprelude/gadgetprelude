import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega o .env da raiz do projeto (../.env)
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não encontrado. Confirma o ficheiro .env na raiz do projeto.")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
