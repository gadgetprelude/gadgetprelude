from db import Base
from sqlalchemy import String, DateTime, Text, Integer, ForeignKey, Column,Time, Boolean,ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from datetime import datetime

class CalendarConnection(Base):
    __tablename__ = "calendar_connections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(20), default="google")
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    calendar_id: Mapped[str] = mapped_column(String(255), nullable=False, default="primary")
    token_json: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)

class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Service(Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), nullable=False)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=True)
    public_token = Column(String, unique=True, nullable=True, index=True)
    

    start_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")

    external_provider: Mapped[str | None] = mapped_column(String(20), nullable=True, default="google")
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_html_link: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ReminderPolicy(Base):
    __tablename__ = "reminder_policies"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)  # ex: "default"
    # offsets em minutos antes do start_at (ex: [1440, 120] => 24h e 2h)
    offsets_minutes_json: Mapped[str] = mapped_column(Text, nullable=False, default='[1440, 120]')
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email")  # email/sms/whatsapp
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), nullable=False)

    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email")
    send_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  
    # pending/sent/failed/cancelled

    template_key: Mapped[str] = mapped_column(String(50), nullable=False, default="reminder")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)  # ex: "gadgetprelude"
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    logo_url = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    instagram_url = Column(String, nullable=True)
    facebook_url = Column(String, nullable=True)
    website_url = Column(String, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    template_key = Column(String, nullable=True)
    frontend_configs = relationship("TenantFrontendConfig", back_populates="tenant")

class TenantFrontendConfig(Base):
    __tablename__ = "tenant_frontend_config"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    template_key = Column(String(50), nullable=False)
    theme_json = Column(JSONB, nullable=False, default=dict)
    texts_json = Column(JSONB, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    tenant = relationship("Tenant", back_populates="frontend_configs")

class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    calendar_email = Column(String, nullable=True)
    calendar_provider = Column(String, nullable=True)
    check_external_calendar_conflicts: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

class ProviderService(Base):
    __tablename__ = "provider_services"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)

class ProviderAvailability(Base):
    __tablename__ = "provider_availability"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    weekday = Column(Integer, nullable=False)  # 0=segunda ... 6=domingo
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class AdminUserTenant(Base):
    __tablename__ = "admin_user_tenants"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class AdminUserPermission(Base):
    __tablename__ = "admin_user_permissions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False)
    permission_key: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
