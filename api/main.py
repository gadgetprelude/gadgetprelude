import os
import json
import requests
import secrets
from pydantic import BaseModel
from agent import run_agent
from fastapi import Header
from models import Tenant, TenantFrontendConfig
from models import ReminderPolicy, Reminder  
from pydantic import BaseModel, EmailStr
from datetime import datetime
from models import CalendarConnection, Contact, Service, Appointment, Provider, ProviderService
from datetime import datetime, date, time, timedelta, timezone
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from dotenv import load_dotenv
from itsdangerous import URLSafeSerializer
from sqlalchemy.orm import Session
from datetime import timedelta
from models import Reminder, ProviderAvailability
from db import Base, engine, get_db
from models import CalendarConnection
from google_oauth import build_flow, creds_from_token_json, calendar_service_from_creds
from pathlib import Path
from typing import List
from fastapi.middleware.cors import CORSMiddleware
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from calendar_adapters.factory import get_calendar_adapter
from microsoft_oauth import exchange_microsoft_code_for_tokens
from itsdangerous import URLSafeSerializer, BadSignature
from fastapi import Response, Request
from models import AdminUser, AdminUserTenant, AdminUserPermission
from admin_security import hash_password, verify_password


load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

SESSION_COOKIE_NAME = "gp_admin_session"
ADMIN_COOKIE_SECURE = os.getenv("ADMIN_COOKIE_SECURE", "false").lower() == "true"
session_serializer = URLSafeSerializer(os.getenv("SECRET_KEY", "dev-secret"), salt="gp-admin-session")

app = FastAPI(title="gadgetprelude API")
        #"https://gadgetprelude.onrender.com",
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "https://book.gadgetprelude.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ContactCreate(BaseModel):
    name: str
    email: EmailStr | None = None
    phone: str | None = None

class ServiceCreate(BaseModel):
    name: str
    duration_minutes: int = 30

class AppointmentCreate(BaseModel):
    calendar_email: EmailStr
    contact_id: int
    service_id: int
    start_at: datetime  # ISO 8601 com timezone
    description: str | None = None

class AppointmentReschedule(BaseModel):
    calendar_email: EmailStr
    new_start_at: datetime

class AppointmentCancel(BaseModel):
    calendar_email: EmailStr
    reason: str | None = None

class AgentChatIn(BaseModel):
    message: str

class PublicProviderOut(BaseModel):
    id: int
    name: str

class PublicBookingCreate(BaseModel):
    tenant_key: str = "default"
    #calendar_email: str
    service_id: int
    provider_id: int
    start_at: datetime
    customer_name: str
    customer_email: str

class PublicCancelByToken(BaseModel):
    token: str

class PublicRescheduleByToken(BaseModel):
    token: str
    new_start_at: datetime

# cria tabelas (MVP). Em produção: migrations (alembic).
Base.metadata.create_all(bind=engine)

serializer = URLSafeSerializer(os.getenv("SESSION_SECRET", "dev"))

ADMIN_PERMISSION_KEYS = [
    "general.view",
    "general.edit",
    "branding.view",
    "branding.edit",
    "texts.view",
    "texts.edit",
    "operations.providers.view",
    "operations.providers.edit",
    "operations.services.view",
    "operations.services.edit",
    "operations.provider_services.view",
    "operations.provider_services.edit",
    "operations.availability.view",
    "operations.availability.edit",
    "operations.calendar_links.view",
    "operations.calendar_links.edit",
]


@app.get("/cors-test")
def cors_test():
    return {"ok": True}

@app.get("/")
def home():
    return {"ok": True, "service": "gadgetprelude", "time": datetime.now(timezone.utc).isoformat()}

def create_admin_session_cookie(response: Response, user_id: int):
    session_value = session_serializer.dumps({"user_id": user_id})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_value,
        httponly=True,
        secure=ADMIN_COOKIE_SECURE,
        samesite="none",
        max_age=60 * 60 * 8,
        path="/"
    )

def clear_admin_session_cookie(response: Response):
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/"
    )

def get_current_admin_user(request: Request, db: Session):
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)

    if not cookie_value:
        return None

    try:
        data = session_serializer.loads(cookie_value)
        user_id = data.get("user_id")
    except BadSignature:
        return None

    if not user_id:
        return None

    user = db.query(AdminUser).filter(AdminUser.id == user_id, AdminUser.is_active == True).first()
    return user

def require_admin_user(request: Request, db: Session):
    user = get_current_admin_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

def require_superuser(request: Request, db: Session):
    user = require_admin_user(request, db)
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser required")
    return user

def user_has_tenant_access(db: Session, user: AdminUser, tenant_id: int) -> bool:
    if user.is_superuser:
        return True

    link = (
        db.query(AdminUserTenant)
        .filter(
            AdminUserTenant.user_id == user.id,
            AdminUserTenant.tenant_id == tenant_id
        )
        .first()
    )
    return link is not None
def user_has_permission(db: Session, user: AdminUser, permission_key: str) -> bool:
    if user.is_superuser:
        return True

    perm = (
        db.query(AdminUserPermission)
        .filter(
            AdminUserPermission.user_id == user.id,
            AdminUserPermission.permission_key == permission_key
        )
        .first()
    )
    return perm is not None

def require_tenant_access(db: Session, user: AdminUser, tenant_id: int):
    if not user_has_tenant_access(db, user, tenant_id):
        raise HTTPException(status_code=403, detail="No access to this tenant")


def require_permission(db: Session, user: AdminUser, permission_key: str):
    if not user_has_permission(db, user, permission_key):
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission_key}")


@app.get("/auth/google/start")
def google_start(tenant_key: str = "default"):
    state = serializer.dumps({
        "ts": datetime.utcnow().isoformat(),
        "tenant_key": (tenant_key or "default").strip()
    })

    flow = build_flow(state=state)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(auth_url)

@app.get("/auth/calendar/start")
def calendar_start(tenant_key: str, provider_id: int, db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)

    provider = (
        db.query(Provider)
        .filter(
            Provider.id == provider_id,
            Provider.tenant_id == tenant.id
        )
        .first()
    )

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    calendar_provider = (provider.calendar_provider or "").strip().lower()

    if not calendar_provider:
        raise HTTPException(status_code=400, detail="Provider calendar_provider is not configured")

    adapter = get_calendar_adapter(calendar_provider)
    return adapter.build_auth_start_response(
        tenant_key=tenant.key,
        provider_id=provider.id,
        serializer=serializer
    )
@app.get("/auth/google/callback")
def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    try:
        data = serializer.loads(state)
        tenant_key = (data.get("tenant_key") or "default").strip()
        provider_id = data.get("provider_id")
        calendar_provider = (data.get("calendar_provider") or "google").strip().lower()
        tenant = db.query(Tenant).filter(Tenant.key == tenant_key).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state")

    flow = build_flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Obter um identificador fiável do "primary calendar" (normalmente é o email)
    service = calendar_service_from_creds(creds)
    primary = service.calendarList().get(calendarId="primary").execute()
    email = primary.get("id") or "unknown"
    calendar_id = primary.get("id") or "primary"


    token_json = creds.to_json()

    existing = (
        db.query(CalendarConnection)
        .filter(
            CalendarConnection.tenant_id == tenant.id,
            CalendarConnection.email == email,
            CalendarConnection.provider == calendar_provider
        )
        .first()
    )

    if existing:
        existing.token_json = token_json
        existing.calendar_id = calendar_id
        db.add(existing)
    else:
        conn = CalendarConnection(
            tenant_id=tenant.id,
            provider=calendar_provider,
            email=email,
            calendar_id=calendar_id,
            token_json=token_json
        )
        db.add(conn)

    db.commit()

    return HTMLResponse(f"""
    <h2>Google ligado com sucesso ✅</h2>
    <p>Email: <b>{email}</b></p>
    <p>Agora testa: <a href="/google/test-create-event?email={email}">criar evento</a></p>
    """)

@app.get("/auth/google/status")
def google_status(db: Session = Depends(get_db)):
    conns = db.query(CalendarConnection).all()
    return [{"id": c.id, "email": c.email, "calendar_id": c.calendar_id} for c in conns]

@app.get("/google/test-create-event")
def test_create_event(email: str, db: Session = Depends(get_db)):
    conn = db.query(CalendarConnection).filter(CalendarConnection.email == email).first()
    if not conn:
        raise HTTPException(status_code=404, detail="No calendar connection for that email")

    creds = creds_from_token_json(conn.token_json)
    service = calendar_service_from_creds(creds)

    start = datetime.now(timezone.utc) + timedelta(minutes=10)
    end = start + timedelta(minutes=30)

    event = {
        "summary": "GadgetPrelude teste",
        "description": "Evento criado pelo MVP FastAPI",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }

    created = service.events().insert(calendarId=conn.calendar_id, body=event).execute()
    return {"created": True, "eventId": created.get("id"), "htmlLink": created.get("htmlLink")}

@app.get("/auth/microsoft/callback")
def microsoft_callback(code: str, state: str, db: Session = Depends(get_db)):
    try:
        data = serializer.loads(state)

        tenant_key = (data.get("tenant_key") or "default").strip()
        provider_id = data.get("provider_id")
        calendar_provider = (data.get("calendar_provider") or "microsoft").strip().lower()

        tenant = db.query(Tenant).filter(Tenant.key == tenant_key).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state")

    adapter = get_calendar_adapter(calendar_provider)
    result = adapter.handle_callback(code)

    email = result["email"]
    calendar_id = result["calendar_id"]
    token_json = json.dumps(result["token_data"])

    existing = (
        db.query(CalendarConnection)
        .filter(
            CalendarConnection.tenant_id == tenant.id,
            CalendarConnection.email == email,
            CalendarConnection.provider == calendar_provider
        )
        .first()
    )

    if existing:
        existing.token_json = token_json
        existing.calendar_id = calendar_id
        db.add(existing)
    else:
        conn = CalendarConnection(
            tenant_id=tenant.id,
            provider=calendar_provider,
            email=email,
            calendar_id=calendar_id,
            token_json=token_json
        )
        db.add(conn)

    db.commit()

    return HTMLResponse(f"""
    <h2>Microsoft ligado com sucesso ✅</h2>
    <p>Email: <b>{email}</b></p>
    <p>Tenant: <b>{tenant.key}</b></p>
    """)

def get_tenant(db: Session, x_tenant_key: str | None):
    key = (x_tenant_key or "default").strip()
    if key.lower() in ("", "string", "null", "none"):
        key = "default"

    t = db.query(Tenant).filter(Tenant.key == key).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return t


@app.post("/contacts")
def create_contact(payload: ContactCreate, db: Session = Depends(get_db), x_tenant_key: str = Header(default="default", alias="X-Tenant-Key")):
    tenant = get_tenant(db, x_tenant_key)
    c = Contact(tenant_id=tenant.id,name=payload.name, email=str(payload.email) if payload.email else None, phone=payload.phone)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "name": c.name, "email": c.email, "phone": c.phone}

@app.post("/services")
def create_service(payload: ServiceCreate, db: Session = Depends(get_db), x_tenant_key: str = Header(default="default", alias="X-Tenant-Key")):
    tenant = get_tenant(db, x_tenant_key)
    s = Service(tenant_id=tenant.id,name=payload.name, duration_minutes=payload.duration_minutes)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "name": s.name, "duration_minutes": s.duration_minutes}

@app.post("/appointments")
def create_appointment(payload: AppointmentCreate, db: Session = Depends(get_db), x_tenant_key: str = Header(default="default", alias="X-Tenant-Key")):
    
    tenant = get_tenant(db, x_tenant_key)
    # buscar serviço e contacto
    contact = db.get(Contact, payload.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    service = db.get(Service, payload.service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # buscar ligação ao google calendar
    conn = db.query(CalendarConnection).filter(CalendarConnection.tenant_id == tenant.id,CalendarConnection.email == str(payload.calendar_email)).first()
    if not conn:
        raise HTTPException(status_code=404, detail="No Google Calendar connection for that email. Do OAuth first.")

    start_at = payload.start_at
    end_at = start_at + timedelta(minutes=service.duration_minutes)

    # criar evento no Google
    creds = creds_from_token_json(conn.token_json)
    service_api = calendar_service_from_creds(creds)

    event_body = {
        "summary": f"{service.name} - {contact.name}",
        "description": payload.description or "",
        "start": {"dateTime": start_at.isoformat()},
        "end": {"dateTime": end_at.isoformat()},
    }
    created = service_api.events().insert(calendarId=conn.calendar_id, body=event_body).execute()
    
    if contact.tenant_id != tenant.id:
       raise HTTPException(status_code=403, detail="Contact not in tenant")
    if service.tenant_id != tenant.id:
       raise HTTPException(status_code=403, detail="Service not in tenant")

    # guardar appointment interno
    ap = Appointment(
        tenant_id=tenant.id,
	contact_id=contact.id,
        service_id=service.id,
        start_at=start_at,
        end_at=end_at,
        status="scheduled",
        external_provider="google",
        external_event_id=created.get("id"),
        external_html_link=created.get("htmlLink"),
    )
    db.add(ap)
    db.commit()
    db.refresh(ap)
    schedule_reminders_for_appointment(db, ap, policy_key="default")


    return {
        "appointment_id": ap.id,
        "google_event_id": ap.external_event_id,
        "google_link": ap.external_html_link,
        "start_at": ap.start_at.isoformat(),
        "end_at": ap.end_at.isoformat(),
    }

@app.post("/appointments/{appointment_id}/reschedule")
def reschedule_appointment(appointment_id: int, payload: AppointmentReschedule, db: Session = Depends(get_db), x_tenant_key: str = Header(default="default", alias="X-Tenant-Key")):
    tenant = get_tenant(db, x_tenant_key)
    ap = db.get(Appointment, appointment_id)
    if not ap:
        raise HTTPException(status_code=404, detail="Appointment not found")

    # buscar serviço (para calcular nova hora fim)
    svc = db.get(Service, ap.service_id)
    if not svc:
        raise HTTPException(status_code=500, detail="Service missing for appointment")

    conn = db.query(CalendarConnection).filter(CalendarConnection.tenant_id == tenant.id,CalendarConnection.email == str(payload.calendar_email)).first()
    if not conn:
        raise HTTPException(status_code=404, detail="No Google Calendar connection for that email")

    if not ap.external_event_id:
        raise HTTPException(status_code=400, detail="Appointment has no external event id")

    new_start = payload.new_start_at
    new_end = new_start + timedelta(minutes=svc.duration_minutes)

    # update no Google
    creds = creds_from_token_json(conn.token_json)
    service_api = calendar_service_from_creds(creds)

    patch_body = {
        "start": {"dateTime": new_start.isoformat()},
        "end": {"dateTime": new_end.isoformat()},
    }
    updated = service_api.events().patch(
        calendarId=conn.calendar_id,
        eventId=ap.external_event_id,
        body=patch_body
    ).execute()

    # update interno
    ap.start_at = new_start
    ap.end_at = new_end
    ap.status = "rescheduled"
    ap.external_html_link = updated.get("htmlLink") or ap.external_html_link

    db.add(ap)
    db.commit()
    db.refresh(ap)
    schedule_reminders_for_appointment(db, ap, policy_key="default")


    return {
        "appointment_id": ap.id,
        "status": ap.status,
        "start_at": ap.start_at.isoformat(),
        "end_at": ap.end_at.isoformat(),
        "google_link": ap.external_html_link,
    }

@app.post("/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id: int, payload: AppointmentCancel, db: Session = Depends(get_db), x_tenant_key: str = Header(default="default", alias="X-Tenant-Key")):
    tenant = get_tenant(db, x_tenant_key)
    ap = db.get(Appointment, appointment_id)
    if not ap:
        raise HTTPException(status_code=404, detail="Appointment not found")

    conn = db.query(CalendarConnection).filter(CalendarConnection.tenant_id == tenant.id,CalendarConnection.email == str(payload.calendar_email)).first()
    if not conn:
        raise HTTPException(status_code=404, detail="No Google Calendar connection for that email")

    # apagar no Google (se existir)
    if ap.external_event_id:
        creds = creds_from_token_json(conn.token_json)
        service_api = calendar_service_from_creds(creds)
        service_api.events().delete(
            calendarId=conn.calendar_id,
            eventId=ap.external_event_id
        ).execute()

    # update interno
    ap.status = "cancelled"
    db.add(ap)
    db.commit()
    db.refresh(ap)
    cancel_pending_reminders(db, ap.id)

    return {"appointment_id": ap.id, "status": ap.status}

def ensure_default_policy(db: Session):
    existing = db.query(ReminderPolicy).filter(ReminderPolicy.key == "default").first()
    if not existing:
        p = ReminderPolicy(key="default", offsets_minutes_json=json.dumps([1440, 120]), channel="email")
        db.add(p)
        db.commit()

def ensure_default_tenant(db: Session):
    t = db.query(Tenant).filter(Tenant.key == "default").first()
    if not t:
        t = Tenant(key="default", name="GadgetPrelude (Default)")
        db.add(t)
        db.commit()

@app.on_event("startup")
def startup_seed():
    from db import SessionLocal
    db = SessionLocal()
    try:
        ensure_default_tenant(db)
        ensure_default_policy(db)
    finally:
        db.close()

def cancel_pending_reminders(db: Session, appointment_id: int):
    db.query(Reminder).filter(
        Reminder.appointment_id == appointment_id,
        Reminder.status == "pending"
    ).update({"status": "cancelled"})
    db.commit()

def schedule_reminders_for_appointment(db: Session, appointment: Appointment, policy_key: str = "default"):
    import json
    policy = db.query(ReminderPolicy).filter(ReminderPolicy.key == policy_key).first()
    if not policy:
        raise HTTPException(status_code=500, detail=f"Reminder policy '{policy_key}' not found")

    offsets = json.loads(policy.offsets_minutes_json)

    # remove pendentes antigos
    cancel_pending_reminders(db, appointment.id)

    for minutes_before in offsets:
        send_at = appointment.start_at - timedelta(minutes=int(minutes_before))
        r = Reminder(
            appointment_id=appointment.id,
            channel=policy.channel,
            send_at=send_at,
            status="pending",
            template_key=f"reminder_{minutes_before}m",
            payload_json="{}",
        )
        db.add(r)

    db.commit()

@app.post("/debug/reminder-policy/set")
def debug_set_policy(db: Session = Depends(get_db)):
    import json
    p = db.query(ReminderPolicy).filter(ReminderPolicy.key == "default").first()
    if not p:
        p = ReminderPolicy(key="default", offsets_minutes_json=json.dumps([2]), channel="email")
        db.add(p)
    else:
        p.offsets_minutes_json = json.dumps([2])
        p.channel = "email"
        db.add(p)
    db.commit()
    return {"ok": True, "policy": "default", "offsets": [2]}


@app.post("/debug/reminder/create-now/{appointment_id}")
def debug_create_reminder_now(appointment_id: int, db: Session = Depends(get_db)):
    ap = db.get(Appointment, appointment_id)
    if not ap:
        raise HTTPException(status_code=404, detail="Appointment not found")

    send_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    r = Reminder(
        appointment_id=appointment_id,
        channel="email",
        send_at=send_at,
        status="pending",
        template_key="debug_now",
        payload_json="{}",
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"ok": True, "reminder_id": r.id, "send_at": r.send_at.isoformat()}

@app.get("/debug/reminders")
def debug_list_reminders(db: Session = Depends(get_db)):
    rs = db.query(Reminder).order_by(Reminder.id.desc()).limit(20).all()
    return [
        {"id": r.id, "appointment_id": r.appointment_id, "status": r.status, "send_at": r.send_at.isoformat()}
        for r in rs
    ]

@app.post("/agent/chat")
def agent_chat(payload: AgentChatIn, db: Session = Depends(get_db), x_tenant_key: str = Header(default="default", alias="X-Tenant-Key")):
    tenant = get_tenant(db, x_tenant_key)
    out = run_agent(db, tenant, payload.message)
    return out

@app.post("/public/book")
def public_book(payload: PublicBookingCreate, db: Session = Depends(get_db)):
    tenant = get_tenant(db, payload.tenant_key)
    provider = db.get(Provider, payload.provider_id)
    public_token = secrets.token_urlsafe(32)

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if provider.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Provider not in tenant")

    if not provider.calendar_email:
        raise HTTPException(status_code=400, detail="Provider calendar not configured")

    calendar_provider = (provider.calendar_provider or "").strip().lower()

    if not calendar_provider:
        raise HTTPException(status_code=400, detail="Provider calendar_provider not configured")

    conn = db.query(CalendarConnection).filter(
        CalendarConnection.tenant_id == tenant.id,
        CalendarConnection.email == provider.calendar_email,
        CalendarConnection.provider == calendar_provider
    ).first()

    if not conn:
        raise HTTPException(status_code=404, detail="No Google Calendar connection for this provider")

    service = db.get(Service, payload.service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    start = payload.start_at
    now_utc = datetime.now(timezone.utc)

    if start <= now_utc:
        raise HTTPException(status_code=400, detail="Cannot create bookings in the past")

    end = start + timedelta(minutes=service.duration_minutes)

    # criar contacto automaticamente
    contact = Contact(
        tenant_id=tenant.id,
        name=payload.customer_name,
        email=payload.customer_email
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)

    adapter = get_calendar_adapter(calendar_provider)

    created = adapter.create_event(
        connection=conn,
        provider=provider,
        service_obj=service,
        customer_name=payload.customer_name,
        customer_email=payload.customer_email,
        start_at=start,
        end_at=end,
    )
    db.add(conn)
    ap = Appointment(
        tenant_id=tenant.id,
        contact_id=contact.id,
        provider_id=provider.id,
        service_id=service.id,
        start_at=start,
        end_at=end,
        status="scheduled",
        public_token=public_token,
        external_event_id=created.get("external_event_id"),
        external_html_link=created.get("external_html_link")
    )

    db.add(ap)
    db.commit()
    db.refresh(ap)

    # criar reminder 24h antes
    reminder_time = ap.start_at - timedelta(days=1)

    reminder = Reminder(
        tenant_id=ap.tenant_id,
        appointment_id=ap.id,
        send_at=reminder_time,
        status="pending",
        channel="email",
        template_key="reminder",
        payload_json="{}"
    )

    db.add(reminder)
    db.commit()

    return {
        "ok": True,
        "message": "Booking criado com sucesso",
        "google_link": created.get("htmlLink"),
        "public_token": public_token
    }

def to_utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

@app.get("/public/availability")
def public_availability(
    tenant_key: str,
    provider_id: int,
    service_id: int,
    date: str,
    db: Session = Depends(get_db)
):
    tenant = get_tenant(db, tenant_key)

    provider = (
        db.query(Provider)
        .filter(
            Provider.id == provider_id,
            Provider.tenant_id == tenant.id
        )
        .first()
    )
    if not provider:
        raise HTTPException(status_code=404, detail="Prestador não encontrado.")

    service = (
        db.query(Service)
        .filter(
            Service.id == service_id,
            Service.tenant_id == tenant.id
        )
        .first()
    )
    if not service:
        raise HTTPException(status_code=404, detail="Serviço não encontrado.")

    relation = (
        db.query(ProviderService)
        .filter(
            ProviderService.tenant_id == tenant.id,
            ProviderService.provider_id == provider.id,
            ProviderService.service_id == service.id
        )
        .first()
    )
    if not relation:
        raise HTTPException(status_code=400, detail="Este prestador não executa esse serviço.")

    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail="Data inválida. Usa YYYY-MM-DD.")

    weekday = target_date.weekday()  # segunda=0 ... domingo=6

    availability_rows = (
        db.query(ProviderAvailability)
        .filter(
            ProviderAvailability.tenant_id == tenant.id,
            ProviderAvailability.provider_id == provider.id,
            ProviderAvailability.weekday == weekday,
            ProviderAvailability.is_active == True
        )
        .order_by(ProviderAvailability.start_time.asc())
        .all()
    )

    if not availability_rows:
        return {"slots": []}

    day_start = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)

    existing_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.provider_id == provider.id,
            Appointment.status.in_(["scheduled", "rescheduled"]),
            Appointment.start_at >= day_start,
            Appointment.start_at <= day_end
        )
        .order_by(Appointment.start_at.asc())
        .all()
    )

    external_busy_intervals = []

    if provider.check_external_calendar_conflicts:
        calendar_provider = (provider.calendar_provider or "").strip().lower()
        calendar_email = (provider.calendar_email or "").strip()

        if calendar_provider and calendar_email:
            conn = (
                db.query(CalendarConnection)
                .filter(
                    CalendarConnection.tenant_id == tenant.id,
                    CalendarConnection.email == calendar_email,
                    CalendarConnection.provider == calendar_provider
                )
                .first()
            )

            if conn:
                adapter = get_calendar_adapter(calendar_provider)

                try:
                    external_busy_intervals = adapter.get_busy_intervals(
                        connection=conn,
                        start_at=day_start,
                        end_at=day_end,
                    )
                    db.add(conn)
                    db.commit()
                except HTTPException:
                    # Se o provider não suportar ainda ou falhar, não partimos a página.
                    external_busy_intervals = []
                except Exception:
                    external_busy_intervals = []

    duration = timedelta(minutes=service.duration_minutes)
    step = timedelta(minutes=30)

    slots = []

    for row in availability_rows:
        window_start = datetime.combine(target_date, row.start_time).replace(tzinfo=timezone.utc)
        window_end = datetime.combine(target_date, row.end_time).replace(tzinfo=timezone.utc)

        current = window_start

        while current + duration <= window_end:
            slot_start = current
            slot_end = current + duration

            overlaps = False
            for ap in existing_appointments:
                ap_start = to_utc_aware(ap.start_at)
                ap_end = to_utc_aware(ap.end_at)

                if slot_start < ap_end and slot_end > ap_start:
                    overlaps = True
                    break

            if not overlaps and external_busy_intervals:
                for busy in external_busy_intervals:
                    busy_start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00"))
                    busy_end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00"))

                    if slot_start < busy_end and slot_end > busy_start:
                        overlaps = True
                        break

            if not overlaps:
                slots.append(slot_start.isoformat())

            current += step

    return {"slots": slots}

def get_onboarding_ops_for_tenant(db: Session, tenant_key: str):
    tenant = get_tenant(db, tenant_key)

    frontend_config = (
        db.query(TenantFrontendConfig)
        .filter(
            TenantFrontendConfig.tenant_id == tenant.id,
            TenantFrontendConfig.is_active == True
        )
        .first()
    )

    if not frontend_config or not frontend_config.texts_json:
        return {"services": [], "providers": []}

    texts = frontend_config.texts_json or {}
    onboarding_ops = texts.get("onboarding_ops") or {}

    return {
        "services": onboarding_ops.get("services", []),
        "providers": onboarding_ops.get("providers", [])
    }

@app.get("/public/services")
def public_services(tenant_key: str, provider_id: int, db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)

    provider = (
        db.query(Provider)
        .filter(
            Provider.id == provider_id,
            Provider.tenant_id == tenant.id
        )
        .first()
    )

    if not provider:
        raise HTTPException(status_code=404, detail="Prestador não encontrado.")

    rows = (
        db.query(ProviderService, Service)
        .join(Service, Service.id == ProviderService.service_id)
        .filter(
            ProviderService.tenant_id == tenant.id,
            ProviderService.provider_id == provider.id,
            Service.tenant_id == tenant.id
        )
        .order_by(Service.name.asc())
        .all()
    )

    return [
        {
            "id": service.id,
            "name": service.name,
            "duration_minutes": service.duration_minutes
        }
        for _, service in rows
    ]

def get_default_frontend_texts():
    return {
        "page_title": "Marcar serviço",
        "subtitle": "Escolhe o serviço, a data e o horário.",
        "provider_label": "Prestador",
        "service_label": "Serviço",
        "date_label": "Data",
        "load_slots_button": "Ver horários disponíveis",
        "name_label": "Nome",
        "name_placeholder": "O seu nome",
        "email_label": "Email",
        "email_placeholder": "O seu email",
        "book_button": "Confirmar marcação",
        "footer_note": "Após a confirmação, receberás um convite por email.",
        "success_message": "Marcação criada com sucesso.",
        "manage_booking_link": "Gerir marcação",

        "manage_page_title": "Gerir marcação",
        "manage_page_subtitle": "Consulta os detalhes da tua marcação e gere-a sem login.",
        "customer_label": "Cliente",
        "manage_email_label": "Email",
        "manage_service_label": "Serviço",
        "manage_provider_label": "Prestador",
        "start_at_label": "Início",
        "status_label": "Estado",
        "new_date_label": "Nova data",
        "cancel_button": "Cancelar marcação",
        "show_reschedule_button": "Reagendar marcação",
        "confirm_reschedule_button": "Confirmar reagendamento",
        "cancel_confirm_text": "Tens a certeza que queres cancelar esta marcação?",

        "loading_slots_button": "A carregar horários...",
        "loading_book_button": "A confirmar marcação...",
        "loading_cancel_button": "A cancelar...",
        "loading_reschedule_button": "A reagendar...",

        "validation_select_slot": "Escolhe um horário.",
        "validation_fill_name_email": "Preenche nome e email.",
        "validation_select_provider": "Escolhe um prestador.",
        "validation_select_service_date": "Escolhe um serviço e uma data.",
        "validation_past_date": "Não podes escolher uma data no passado.",
        "validation_select_new_date": "Escolhe uma nova data.",
        "validation_select_new_slot": "Escolhe um novo horário.",

        "error_load_page": "Erro ao carregar a página de marcação.",
        "error_load_booking": "Erro ao carregar a marcação.",
        "error_load_slots": "Erro ao carregar horários.",
        "error_create_booking": "Erro ao criar marcação.",
        "error_server_create_booking": "Erro de ligação ao servidor ao criar marcação.",
        "error_server_cancel_booking": "Erro de ligação ao servidor ao cancelar marcação.",
        "error_server_reschedule_booking": "Erro de ligação ao servidor ao reagendar marcação.",

        "success_cancel_booking": "Marcação cancelada com sucesso.",
        "success_reschedule_booking": "Marcação reagendada com sucesso."
    }

def get_default_frontend_theme():
    return {
        "primary_color": "#2563eb",
        "background_color": "#f5f7fb",
        "card_background_color": "#ffffff",
        "text_color": "#1f2937"
    }

@app.get("/public/config")
def public_config(tenant_key: str = "default", db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)
    
    # buscar config ativa
    frontend_config = (
        db.query(TenantFrontendConfig)
        .filter(
            TenantFrontendConfig.tenant_id == tenant.id,
            TenantFrontendConfig.is_active == True
        )
        .first()
    )

    # defaults se não existir config
    theme = frontend_config.theme_json if frontend_config else {}
    texts = frontend_config.texts_json if frontend_config else {}
    template_key = frontend_config.template_key if frontend_config else (tenant.template_key or "default")

    #conn = (
    #    db.query(CalendarConnection)
    #    .filter(CalendarConnection.tenant_id == tenant.id)
    #    .first()
    #)
    default_texts = get_default_frontend_texts()
    default_theme = get_default_frontend_theme()

    merged_texts = {**default_texts, **texts}
    merged_theme = {**default_theme, **theme}

    return {
        "tenant_key": tenant.key,
        "business_name": tenant.name,
        "template_key": template_key,

        # branding
        "logo_url": merged_theme.get("logo_url") or tenant.logo_url,
        "phone": tenant.phone,
        "instagram_url": tenant.instagram_url,
        "facebook_url": tenant.facebook_url,
        "website_url": tenant.website_url,

        # cores dinâmicas
        "primary_color": merged_theme.get("primary_color", "#2563eb"),
        "background_color": merged_theme.get("background_color", "#f5f7fb"),
        "card_background_color": merged_theme.get("card_background_color", "#ffffff"),
        "text_color": merged_theme.get("text_color", "#1f2937"),

        # textos principais
        "subtitle": merged_texts.get("subtitle", "Escolhe o serviço, a data e o horário."),
        "success_message": merged_texts.get("success_message", "Marcação criada com sucesso."),

        # todos os textos
        "texts": merged_texts
    }

@app.post("/admin/auth/login")
def admin_login(payload: dict = Body(...), db: Session = Depends(get_db)):
    print("ENTROU NO LOGIN")
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    user = db.query(AdminUser).filter(AdminUser.email == email, AdminUser.is_active == True).first()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    response = JSONResponse({
        "ok": True,
        "email": user.email,
        "is_superuser": user.is_superuser
    })

    create_admin_session_cookie(response, user.id)
    return response

@app.post("/admin/auth/logout")
def admin_logout():
    response = JSONResponse({"ok": True})
    clear_admin_session_cookie(response)
    return response

@app.get("/admin/auth/me")
def admin_me(request: Request, db: Session = Depends(get_db)):
    user = require_admin_user(request, db)

    tenant_links = (
        db.query(AdminUserTenant, Tenant)
        .join(Tenant, Tenant.id == AdminUserTenant.tenant_id)
        .filter(AdminUserTenant.user_id == user.id)
        .all()
    )

    permissions = (
        db.query(AdminUserPermission.permission_key)
        .filter(AdminUserPermission.user_id == user.id)
        .all()
    )

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "is_superuser": user.is_superuser
        },
        "tenants": [
            {
                "id": tenant.id,
                "key": tenant.key,
                "name": tenant.name
            }
            for _, tenant in tenant_links
        ],
        "permissions": [p[0] for p in permissions]
    }

@app.get("/admin/frontend-config")
def admin_get_frontend_config(request: Request, tenant_key: str, db: Session = Depends(get_db)):
    user = require_admin_user(request, db)
    tenant = get_tenant(db, tenant_key)
    
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "general.view")

    frontend_config = get_active_frontend_config(db, tenant.id)

    if not frontend_config:
        return {
            "tenant_key": tenant.key,
            "tenant_name": tenant.name,
            "template_key": tenant.template_key or "default",
            "theme_json": get_default_frontend_theme(),
            "texts_json": get_default_frontend_texts(),
            "phone": tenant.phone,
            "instagram_url": tenant.instagram_url,
            "facebook_url": tenant.facebook_url,
            "website_url": tenant.website_url,
            "is_active": True
        }

    return {
        "tenant_key": tenant.key,
        "tenant_name": tenant.name,
        "template_key": frontend_config.template_key,
        "theme_json": frontend_config.theme_json or {},
        "texts_json": frontend_config.texts_json or {},
        "phone": tenant.phone,
        "instagram_url": tenant.instagram_url,
        "facebook_url": tenant.facebook_url,
        "website_url": tenant.website_url,
        "is_active": frontend_config.is_active
    }

@app.post("/admin/frontend-config")
def admin_save_frontend_config(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    tenant_key = payload.get("tenant_key")
    template_key = payload.get("template_key", "default")
    theme_json = payload.get("theme_json", {})
    texts_json = payload.get("texts_json", {})
    phone = payload.get("phone")
    instagram_url = payload.get("instagram_url")
    facebook_url = payload.get("facebook_url")
    website_url = payload.get("website_url")

    if not tenant_key:
        raise HTTPException(status_code=400, detail="tenant_key é obrigatório.")

    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "general.edit")

    tenant.phone = phone
    tenant.instagram_url = instagram_url
    tenant.facebook_url = facebook_url
    tenant.website_url = website_url
    frontend_config = get_active_frontend_config(db, tenant.id)

    if frontend_config:
        frontend_config.template_key = template_key
        frontend_config.theme_json = theme_json
        frontend_config.texts_json = texts_json
    else:
        frontend_config = TenantFrontendConfig(
            tenant_id=tenant.id,
            template_key=template_key,
            theme_json=theme_json,
            texts_json=texts_json,
            is_active=True
        )
        db.add(frontend_config)

    db.commit()
    db.refresh(frontend_config)

    return {
        "message": "Configuração guardada com sucesso.",
        "tenant_key": tenant.key,
        "tenant_name": tenant.name,
        "phone": tenant.phone,
        "instagram_url": tenant.instagram_url,
        "facebook_url": tenant.facebook_url,
        "website_url": tenant.website_url,
        "template_key": frontend_config.template_key,
        "theme_json": frontend_config.theme_json,
        "texts_json": frontend_config.texts_json,
        "is_active": frontend_config.is_active
    }

def send_telegram_message(text: str):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    return response.json()

@app.post("/debug/telegram/test")
def debug_telegram_test():
    result = send_telegram_message("✅ Teste Telegram do GadgetPrelude")
    return {"ok": True, "telegram_result": result}

def build_tomorrow_summary(db: Session, tenant_key: str = "default") -> str:
    tenant = get_tenant(db, tenant_key)

    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    day_start = datetime.combine(tomorrow, time(0, 0), tzinfo=timezone.utc)
    day_end = datetime.combine(tomorrow, time(23, 59, 59), tzinfo=timezone.utc)

    appointments = (
        db.query(Appointment)
        .filter(Appointment.tenant_id == tenant.id)
        .filter(Appointment.status.in_(["scheduled", "rescheduled"]))
        .filter(Appointment.start_at >= day_start)
        .filter(Appointment.start_at <= day_end)
        .order_by(Appointment.start_at.asc())
        .all()
    )

    if not appointments:
        return f"📅 Marcações de amanhã ({tomorrow.isoformat()})\n\nSem marcações."

    lines = [f"📅 Marcações de amanhã ({tomorrow.isoformat()})", ""]

    for ap in appointments:
        contact = db.get(Contact, ap.contact_id)
        service = db.get(Service, ap.service_id)

        hour_str = ap.start_at.strftime("%H:%M")
        customer_name = contact.name if contact else "Cliente"
        service_name = service.name if service else "Serviço"

        lines.append(f"{hour_str} - {customer_name} - {service_name}")

    return "\n".join(lines)

@app.post("/debug/telegram/tomorrow-summary")
def debug_telegram_tomorrow_summary(
    tenant_key: str = "default",
    db: Session = Depends(get_db),
):
    message = build_tomorrow_summary(db, tenant_key)
    result = send_telegram_message(message)
    return {"ok": True, "message_sent": message, "telegram_result": result}

@app.get("/public/providers")
def public_list_providers(tenant_key: str, db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)

    providers = (
        db.query(Provider)
        .filter(Provider.tenant_id == tenant.id)
        .order_by(Provider.name.asc())
        .all()
    )

    return [
        {
            "id": p.id,
            "name": p.name
        }
        for p in providers
    ]
@app.get("/public/booking-by-token")
def public_booking_by_token(token: str, db: Session = Depends(get_db)):
    ap = (
        db.query(Appointment)
        .filter(Appointment.public_token == token)
        .first()
    )

    if not ap:
        raise HTTPException(status_code=404, detail="Booking not found")

    contact = db.get(Contact, ap.contact_id)
    service = db.get(Service, ap.service_id)
    provider = db.get(Provider, ap.provider_id) if ap.provider_id else None
    tenant = db.get(Tenant, ap.tenant_id)

    return {
        "appointment_id": ap.id,
        "tenant_key": tenant.key if tenant else None,
        "customer_name": contact.name if contact else None,
        "customer_email": contact.email if contact else None,
        "service_id": ap.service_id,
        "service_name": service.name if service else None,
        "provider_id": ap.provider_id,
        "provider_name": provider.name if provider else None,
        "start_at": ap.start_at.isoformat() if ap.start_at else None,
        "end_at": ap.end_at.isoformat() if ap.end_at else None,
        "status": ap.status,
        "public_token": ap.public_token,
    }

@app.post("/public/cancel-by-token")
def public_cancel_by_token(payload: PublicCancelByToken, db: Session = Depends(get_db)):
    ap = (
        db.query(Appointment)
        .filter(Appointment.public_token == payload.token)
        .first()
    )

    if not ap:
        raise HTTPException(status_code=404, detail="Booking not found")

    if ap.status == "cancelled":
        return {
            "ok": True,
            "message": "Booking already cancelled",
            "appointment_id": ap.id,
            "status": ap.status,
        }

    provider = db.get(Provider, ap.provider_id) if ap.provider_id else None
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if not provider.calendar_email:
        raise HTTPException(status_code=400, detail="Provider calendar not configured")

    calendar_provider = (provider.calendar_provider or "").strip().lower()

    if not calendar_provider:
        raise HTTPException(status_code=400, detail="Provider calendar_provider not configured")

    conn = (
        db.query(CalendarConnection)
        .filter(CalendarConnection.tenant_id == ap.tenant_id)
        .filter(CalendarConnection.email == provider.calendar_email)
        .filter(CalendarConnection.provider == calendar_provider)
        .first()
    )
    db.add(conn)
    if not conn:
        raise HTTPException(status_code=404, detail="No Google Calendar connection for this provider")

    if ap.external_event_id:
        adapter = get_calendar_adapter(calendar_provider)
        adapter.delete_event(
            connection=conn,
            event_id=ap.external_event_id
        )

    ap.status = "cancelled"
    db.add(ap)
    db.commit()
    db.refresh(ap)

    cancel_pending_reminders(db, ap.id)

    return {
        "ok": True,
        "message": "Booking cancelled successfully",
        "appointment_id": ap.id,
        "status": ap.status,
    }

@app.post("/public/reschedule-by-token")
def public_reschedule_by_token(payload: PublicRescheduleByToken, db: Session = Depends(get_db)):
    ap = (
        db.query(Appointment)
        .filter(Appointment.public_token == payload.token)
        .first()
    )

    if not ap:
        raise HTTPException(status_code=404, detail="Booking not found")

    if ap.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot reschedule a cancelled booking")

    provider = db.get(Provider, ap.provider_id) if ap.provider_id else None
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if not provider.calendar_email:
        raise HTTPException(status_code=400, detail="Provider calendar not configured")

    service = db.get(Service, ap.service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    calendar_provider = (provider.calendar_provider or "").strip().lower()

    if not calendar_provider:
        raise HTTPException(status_code=400, detail="Provider calendar_provider not configured")

    conn = (
        db.query(CalendarConnection)
        .filter(CalendarConnection.tenant_id == ap.tenant_id)
        .filter(CalendarConnection.email == provider.calendar_email)
        .filter(CalendarConnection.provider == calendar_provider)
        .first()
    )

    if not conn:
        raise HTTPException(status_code=404, detail="No Google Calendar connection for this provider")

    new_start = payload.new_start_at
    now_utc = datetime.now(timezone.utc)
    if new_start <= now_utc:
        raise HTTPException(status_code=400, detail="Cannot reschedule to the past")

    new_end = new_start + timedelta(minutes=service.duration_minutes)

    if not ap.external_event_id:
        raise HTTPException(status_code=400, detail="Booking has no external event id")

    adapter = get_calendar_adapter(calendar_provider)

    updated = adapter.update_event(
        connection=conn,
        event_id=ap.external_event_id,
        start_at=new_start,
        end_at=new_end,
    )
    db.add(conn)
    ap.start_at = new_start
    ap.end_at = new_end
    ap.status = "rescheduled"
    ap.external_html_link = updated.get("external_html_link") or ap.external_html_link
    db.add(ap)
    db.commit()
    db.refresh(ap)

    schedule_reminders_for_appointment(db, ap, policy_key="default")

    return {
        "ok": True,
        "message": "Booking rescheduled successfully",
        "appointment_id": ap.id,
        "status": ap.status,
        "start_at": ap.start_at.isoformat(),
        "end_at": ap.end_at.isoformat(),
    }

def send_email_reminder(to_email: str, subject: str, html_body: str, tenant_name: str | None = None):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from_name = os.getenv("SMTP_FROM_NAME", "GadgetPrelude")
    smtp_from_email = os.getenv("SMTP_FROM_EMAIL")

    if not smtp_host or not smtp_username or not smtp_password or not smtp_from_email:
        raise RuntimeError("SMTP settings are missing")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    from_name = f"{tenant_name} via GadgetPrelude" if tenant_name else smtp_from_name
    msg["From"] = f"{from_name} <{smtp_from_email}>"
    msg["To"] = to_email

    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.sendmail(smtp_from_email, [to_email], msg.as_string())

def build_customer_reminder_email(ap: Appointment, db: Session) -> tuple[str, str]:
    contact = db.get(Contact, ap.contact_id)
    service = db.get(Service, ap.service_id)
    provider = db.get(Provider, ap.provider_id) if ap.provider_id else None
    tenant = db.get(Tenant, ap.tenant_id)

    customer_name = contact.name if contact else "cliente"
    service_name = service.name if service else "Serviço"
    provider_name = provider.name if provider else "Prestador"
    business_name = tenant.name if tenant else "GadgetPrelude"
    start_str = ap.start_at.strftime("%d/%m/%Y às %H:%M") if ap.start_at else "-"
    manage_url = f"https://book.gadgetprelude.com/manage.html?token={ap.public_token}"

    subject = f"Lembrete da tua marcação - {business_name}"

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #1f2937;">
        <h2>Lembrete da tua marcação</h2>
        <p>Olá {customer_name},</p>
        <p>Este é um lembrete da tua marcação:</p>
        <ul>
          <li><strong>Negócio:</strong> {business_name}</li>
          <li><strong>Serviço:</strong> {service_name}</li>
          <li><strong>Prestador:</strong> {provider_name}</li>
          <li><strong>Data/Hora:</strong> {start_str}</li>
        </ul>
        <p>Podes gerir a tua marcação aqui:</p>
        <p><a href="{manage_url}">Gerir marcação</a></p>
      </body>
    </html>
    """

    return subject, html

@app.post("/debug/email-reminder/{appointment_id}")
def debug_email_reminder(appointment_id: int, db: Session = Depends(get_db)):
    ap = db.get(Appointment, appointment_id)
    if not ap:
        raise HTTPException(status_code=404, detail="Appointment not found")

    contact = db.get(Contact, ap.contact_id)
    if not contact or not contact.email:
        raise HTTPException(status_code=400, detail="Appointment contact has no email")

    tenant = db.get(Tenant, ap.tenant_id)
    subject, html = build_customer_reminder_email(ap, db)
    send_email_reminder(
        contact.email,
        subject,
        html,
        tenant_name=tenant.name if tenant else None
    )

    return {
        "ok": True,
        "message": "Reminder email sent",
        "to": contact.email,
    }

def get_active_frontend_config(db: Session, tenant_id: int):
    return (
        db.query(TenantFrontendConfig)
        .filter(
            TenantFrontendConfig.tenant_id == tenant_id,
            TenantFrontendConfig.is_active == True
        )
        .first()
    )

@app.get("/admin/tenants")
def admin_tenants(request: Request, db: Session = Depends(get_db)):
    user = require_admin_user(request, db)

    if user.is_superuser:
        tenants = db.query(Tenant).order_by(Tenant.name).all()
    else:
        tenants = (
            db.query(Tenant)
            .join(AdminUserTenant, AdminUserTenant.tenant_id == Tenant.id)
            .filter(AdminUserTenant.user_id == user.id)
            .order_by(Tenant.name)
            .all()
        )

    return [
        {"id": t.id, "key": t.key, "name": t.name}
        for t in tenants
    ]
@app.get("/admin/services")
def admin_list_services(request: Request,tenant_key: str, db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.services.view")

    services = (
        db.query(Service)
        .filter(Service.tenant_id == tenant.id)
        .order_by(Service.name.asc())
        .all()
    )

    return [
        {
            "id": service.id,
            "name": service.name,
            "duration_minutes": service.duration_minutes
        }
        for service in services
    ]
@app.post("/admin/services")
def admin_save_services(request: Request,payload: dict = Body(...), db: Session = Depends(get_db)):
    tenant_key = payload.get("tenant_key")
    services = payload.get("services", [])

    if not tenant_key:
        raise HTTPException(status_code=400, detail="tenant_key é obrigatório.")

    if not isinstance(services, list):
        raise HTTPException(status_code=400, detail="services deve ser uma lista.")

    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.services.edit")

    saved_ids = []

    for item in services:
        service_id = item.get("id")
        name = (item.get("name") or "").strip()
        duration_minutes = item.get("duration_minutes")

        if not name:
            continue

        if duration_minutes is None:
            duration_minutes = 30

        if service_id:
            service = (
                db.query(Service)
                .filter(
                    Service.id == service_id,
                    Service.tenant_id == tenant.id
                )
                .first()
            )

            if service:
                service.name = name
                service.duration_minutes = int(duration_minutes)
                db.add(service)
                db.flush()
                saved_ids.append(service.id)
                continue

        service = Service(
            tenant_id=tenant.id,
            name=name,
            duration_minutes=int(duration_minutes)
        )
        db.add(service)
        db.flush()
        saved_ids.append(service.id)

    db.commit()

    saved_services = (
        db.query(Service)
        .filter(Service.tenant_id == tenant.id)
        .order_by(Service.name.asc())
        .all()
    )

    return [
        {
            "id": service.id,
            "name": service.name,
            "duration_minutes": service.duration_minutes
        }
        for service in saved_services
    ]

@app.get("/admin/providers")
def admin_list_providers(request: Request,tenant_key: str, db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.providers.view")

    providers = (
        db.query(Provider)
        .filter(Provider.tenant_id == tenant.id)
        .order_by(Provider.name.asc())
        .all()
    )

    return [
        {
            "id": provider.id,
            "name": provider.name,
            "calendar_email": provider.calendar_email,
            "calendar_provider": provider.calendar_provider,
            "check_external_calendar_conflicts": provider.check_external_calendar_conflicts
        }
        for provider in providers
    ]

@app.post("/admin/providers")
def admin_save_providers(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    tenant_key = payload.get("tenant_key")
    providers = payload.get("providers", [])

    if not tenant_key:
        raise HTTPException(status_code=400, detail="tenant_key é obrigatório.")

    if not isinstance(providers, list):
        raise HTTPException(status_code=400, detail="providers deve ser uma lista.")

    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.providers.edit")

    for item in providers:
        provider_id = item.get("id")
        name = (item.get("name") or "").strip()
        calendar_email = (item.get("calendar_email") or "").strip() or None
        calendar_provider = (item.get("calendar_provider") or "").strip().lower() or None
        check_external_calendar_conflicts = bool(item.get("check_external_calendar_conflicts", False))

        if not name:
            continue

        if provider_id:
            provider = (
                db.query(Provider)
                .filter(
                    Provider.id == provider_id,
                    Provider.tenant_id == tenant.id
                )
                .first()
            )

            if provider:
                provider.name = name
                provider.calendar_email = calendar_email
                provider.calendar_provider = calendar_provider
                provider.check_external_calendar_conflicts = check_external_calendar_conflicts
                db.add(provider)
                db.flush()
                continue

        provider = Provider(
            provider = Provider(
            tenant_id=tenant.id,
            name=name,
            calendar_email=calendar_email,
            calendar_provider=calendar_provider,
            check_external_calendar_conflicts=check_external_calendar_conflicts
            )
        )
        db.add(provider)
        db.flush()

    db.commit()

    saved_providers = (
        db.query(Provider)
        .filter(Provider.tenant_id == tenant.id)
        .order_by(Provider.name.asc())
        .all()
    )

    return [
        {
            "id": provider.id,
            "name": provider.name,
            "calendar_email": provider.calendar_email,
            "calendar_provider": provider.calendar_provider,
            "check_external_calendar_conflicts": provider.check_external_calendar_conflicts
        }
        for provider in saved_providers
    ]

@app.get("/admin/provider-availability")
def admin_get_provider_availability(
    request: Request,
    tenant_key: str,
    provider_id: int,
    db: Session = Depends(get_db)
):
    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.availability.view")

    rows = (
        db.query(ProviderAvailability)
        .filter(
            ProviderAvailability.tenant_id == tenant.id,
            ProviderAvailability.provider_id == provider_id
        )
        .order_by(ProviderAvailability.weekday.asc(), ProviderAvailability.start_time.asc())
        .all()
    )

    return [
        {
            "id": row.id,
            "weekday": row.weekday,
            "start_time": row.start_time.strftime("%H:%M"),
            "end_time": row.end_time.strftime("%H:%M"),
            "is_active": row.is_active
        }
        for row in rows
    ]

@app.post("/admin/provider-availability")
def admin_save_provider_availability(request: Request,payload: dict = Body(...), db: Session = Depends(get_db)):
    tenant_key = payload.get("tenant_key")
    provider_id = payload.get("provider_id")
    availability = payload.get("availability", [])

    if not tenant_key:
        raise HTTPException(status_code=400, detail="tenant_key é obrigatório.")

    if not provider_id:
        raise HTTPException(status_code=400, detail="provider_id é obrigatório.")

    if not isinstance(availability, list):
        raise HTTPException(status_code=400, detail="availability deve ser uma lista.")

    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.availability.edit")

    provider = (
        db.query(Provider)
        .filter(
            Provider.id == provider_id,
            Provider.tenant_id == tenant.id
        )
        .first()
    )

    if not provider:
        raise HTTPException(status_code=404, detail="Prestador não encontrado.")

    for item in availability:
        row_id = item.get("id")
        weekday = item.get("weekday")
        start_time_str = item.get("start_time")
        end_time_str = item.get("end_time")
        is_active = item.get("is_active", True)

        if weekday is None or not start_time_str or not end_time_str:
            continue

        try:
            start_time_obj = datetime.strptime(start_time_str, "%H:%M").time()
            end_time_obj = datetime.strptime(end_time_str, "%H:%M").time()
        except Exception:
            raise HTTPException(status_code=400, detail="Formato de hora inválido. Usa HH:MM.")

        if row_id:
            row = (
                db.query(ProviderAvailability)
                .filter(
                    ProviderAvailability.id == row_id,
                    ProviderAvailability.tenant_id == tenant.id,
                    ProviderAvailability.provider_id == provider.id
                )
                .first()
            )

            if row:
                row.weekday = int(weekday)
                row.start_time = start_time_obj
                row.end_time = end_time_obj
                row.is_active = bool(is_active)
                db.add(row)
                db.flush()
                continue

        if bool(is_active):
            row = ProviderAvailability(
                tenant_id=tenant.id,
                provider_id=provider.id,
                weekday=int(weekday),
                start_time=start_time_obj,
                end_time=end_time_obj,
                is_active=True
            )
            db.add(row)
            db.flush()

    db.commit()

    rows = (
        db.query(ProviderAvailability)
        .filter(
            ProviderAvailability.tenant_id == tenant.id,
            ProviderAvailability.provider_id == provider.id
        )
        .order_by(ProviderAvailability.weekday.asc(), ProviderAvailability.start_time.asc())
        .all()
    )

    return [
        {
            "id": row.id,
            "weekday": row.weekday,
            "start_time": row.start_time.strftime("%H:%M"),
            "end_time": row.end_time.strftime("%H:%M"),
            "is_active": row.is_active
        }
        for row in rows
    ]

@app.get("/admin/provider-services")
def admin_get_provider_services(request: Request,tenant_key: str, db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.provider_services.view")

    rows = (
        db.query(ProviderService)
        .filter(ProviderService.tenant_id == tenant.id)
        .order_by(ProviderService.provider_id.asc(), ProviderService.service_id.asc())
        .all()
    )

    grouped = {}

    for row in rows:
        if row.provider_id not in grouped:
            grouped[row.provider_id] = {
                "provider_id": row.provider_id,
                "service_ids": []
            }
        grouped[row.provider_id]["service_ids"].append(row.service_id)

    return list(grouped.values())


@app.post("/admin/provider-services")
def admin_save_provider_services(request: Request,payload: dict = Body(...), db: Session = Depends(get_db)):
    tenant_key = payload.get("tenant_key")
    relations = payload.get("relations", [])

    if not tenant_key:
        raise HTTPException(status_code=400, detail="tenant_key é obrigatório.")

    if not isinstance(relations, list):
        raise HTTPException(status_code=400, detail="relations deve ser uma lista.")

    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.provider_services.edit")

    for item in relations:
        provider_id = item.get("provider_id")
        service_ids = item.get("service_ids", [])

        if not provider_id:
            continue

        if not isinstance(service_ids, list):
            continue

        provider = (
            db.query(Provider)
            .filter(
                Provider.id == provider_id,
                Provider.tenant_id == tenant.id
            )
            .first()
        )
        if not provider:
            continue

        existing_rows = (
            db.query(ProviderService)
            .filter(
                ProviderService.tenant_id == tenant.id,
                ProviderService.provider_id == provider_id
            )
            .all()
        )

        existing_service_ids = {row.service_id for row in existing_rows}
        new_service_ids = {int(sid) for sid in service_ids}

        for service_id in new_service_ids - existing_service_ids:
            service = (
                db.query(Service)
                .filter(
                    Service.id == service_id,
                    Service.tenant_id == tenant.id
                )
                .first()
            )
            if not service:
                continue

            row = ProviderService(
                tenant_id=tenant.id,
                provider_id=provider_id,
                service_id=service_id
            )
            db.add(row)

        for row in existing_rows:
            if row.service_id not in new_service_ids:
                db.delete(row)

    db.commit()

    rows = (
        db.query(ProviderService)
        .filter(ProviderService.tenant_id == tenant.id)
        .order_by(ProviderService.provider_id.asc(), ProviderService.service_id.asc())
        .all()
    )

    grouped = {}

    for row in rows:
        if row.provider_id not in grouped:
            grouped[row.provider_id] = {
                "provider_id": row.provider_id,
                "service_ids": []
            }
        grouped[row.provider_id]["service_ids"].append(row.service_id)

    return list(grouped.values())

@app.get("/admin/provider-calendar-status")
def admin_provider_calendar_status(request: Request,tenant_key: str, db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.calendar_links.view")

    providers = (
        db.query(Provider)
        .filter(Provider.tenant_id == tenant.id)
        .order_by(Provider.name.asc())
        .all()
    )

    connections = (
        db.query(CalendarConnection)
        .filter(CalendarConnection.tenant_id == tenant.id)
        .all()
    )

    connection_map = {}
    for conn in connections:
        provider_key = (conn.provider or "").strip().lower()
        email_key = (conn.email or "").strip().lower()
        connection_map[(provider_key, email_key)] = conn

    result = []

    for provider in providers:
        calendar_email = (provider.calendar_email or "").strip()
        calendar_provider = (provider.calendar_provider or "").strip().lower()

        status = "not_configured"
        status_label = "Não configurado"
        connected_email = None
        auth_url = None

        if not calendar_email:
            status = "missing_email"
            status_label = "Sem email"
        elif not calendar_provider:
            status = "missing_provider"
            status_label = "Sem provider"
        else:
            conn = connection_map.get((calendar_provider, calendar_email.lower()))

            if conn:
                connected_email = conn.email

                expected_email = (calendar_email or "").strip().lower()
                actual_email = (conn.email or "").strip().lower()

                if expected_email != actual_email:
                    status = "email_mismatch"
                    status_label = "Email diferente"
                elif conn.last_test_status == "reauth_required":
                    status = "reauth_required"
                    status_label = "Reautenticação necessária"
                elif conn.last_test_status == "invalid_credentials":
                    status = "reauth_required"
                    status_label = "Reautenticação necessária"
                elif conn.last_test_status == "test_failed":
                    status = "test_failed"
                    status_label = "Teste falhou"
                else:
                    status = "connected"
                    status_label = "Ligado"
            else:
                status = "pending_auth"
                status_label = "Por autenticar"
            
            if calendar_provider in ("google", "microsoft"):
                auth_url = f"/auth/calendar/start?tenant_key={tenant.key}&provider_id={provider.id}"

        result.append({
            "provider_id": provider.id,
            "provider_name": provider.name,
            "calendar_email": provider.calendar_email,
            "calendar_provider": provider.calendar_provider,
            "status": status,
            "status_label": status_label,
            "connected_email": connected_email,
            "email_matches": (
                ((provider.calendar_email or "").strip().lower() == (connected_email or "").strip().lower())
                if connected_email else None
            ),
            "last_test_at": conn.last_test_at.isoformat() if conn and conn.last_test_at else None,
            "last_test_status": conn.last_test_status if conn else None,
            "last_test_message": conn.last_test_message if conn else None,
            "auth_url": auth_url
        })

    return result

@app.get("/admin/test-calendar-connection")
def admin_test_calendar_connection(
    request: Request,
    tenant_key: str,
    provider_id: int,
    db: Session = Depends(get_db)
):
    tenant = get_tenant(db, tenant_key)
    user = require_admin_user(request, db)
    require_tenant_access(db, user, tenant.id)
    require_permission(db, user, "operations.calendar_links.edit")

    provider = (
        db.query(Provider)
        .filter(
            Provider.id == provider_id,
            Provider.tenant_id == tenant.id
        )
        .first()
    )

    if not provider:
        raise HTTPException(status_code=404, detail="Prestador não encontrado.")

    calendar_email = (provider.calendar_email or "").strip()
    calendar_provider = (provider.calendar_provider or "").strip().lower()

    if not calendar_email:
        raise HTTPException(status_code=400, detail="Prestador sem calendar_email configurado.")

    if not calendar_provider:
        raise HTTPException(status_code=400, detail="Prestador sem calendar_provider configurado.")

    conn = (
        db.query(CalendarConnection)
        .filter(
            CalendarConnection.tenant_id == tenant.id,
            CalendarConnection.email == calendar_email,
            CalendarConnection.provider == calendar_provider
        )
        .first()
    )

    if not conn:
        raise HTTPException(status_code=404, detail="Ligação de calendário não encontrada para este prestador.")

    adapter = get_calendar_adapter(calendar_provider)

    try:
        test_result = adapter.test_connection(conn)

        conn.last_test_at = datetime.now(timezone.utc)
        conn.last_test_status = "connected"
        conn.last_test_message = f"Ligação {calendar_provider.capitalize()} Calendar OK."
        db.add(conn)
        db.commit()

        return {
            "ok": True,
            "status": "connected",
            "provider_id": provider.id,
            "provider_name": provider.name,
            "calendar_provider": calendar_provider,
            "calendar_email": calendar_email,
            "calendar_id": conn.calendar_id,
            "message": f"Ligação {calendar_provider.capitalize()} Calendar OK.",
            "calendar_summary": test_result.get("summary") if isinstance(test_result, dict) else None
        }

    except HTTPException as e:
        conn.last_test_at = datetime.now(timezone.utc)

        detail = e.detail
        if isinstance(detail, dict):
            conn.last_test_status = detail.get("status", "test_failed")
            conn.last_test_message = detail.get("message", "Erro ao testar ligação.")
            db.add(conn)
            db.commit()
            raise

        conn.last_test_status = "test_failed"
        conn.last_test_message = str(detail)
        db.add(conn)
        db.commit()
        raise

    except Exception as e:
        error_text = str(e).lower()
        conn.last_test_at = datetime.now(timezone.utc)

        if "invalid_grant" in error_text or "expired or revoked" in error_text or "token has been expired or revoked" in error_text:
            conn.last_test_status = "reauth_required"
            conn.last_test_message = "Ligação expirada ou revogada. É necessário reautenticar."
            db.add(conn)
            db.commit()

            raise HTTPException(
                status_code=400,
                detail={
                    "ok": False,
                    "status": "reauth_required",
                    "message": "Ligação expirada ou revogada. É necessário reautenticar."
                }
            )

        if "unauthorized" in error_text or "invalid credentials" in error_text:
            conn.last_test_status = "invalid_credentials"
            conn.last_test_message = "Credenciais inválidas. É necessário autenticar novamente."
            db.add(conn)
            db.commit()

            raise HTTPException(
                status_code=400,
                detail={
                    "ok": False,
                    "status": "invalid_credentials",
                    "message": "Credenciais inválidas. É necessário autenticar novamente."
                }
            )

        conn.last_test_status = "test_failed"
        conn.last_test_message = f"Erro ao testar {calendar_provider.capitalize()} Calendar: {str(e)}"
        db.add(conn)
        db.commit()

        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "status": "test_failed",
                "message": f"Erro ao testar {calendar_provider.capitalize()} Calendar.",
                "technical_error": str(e)
            }
        )
    
@app.get("/admin/users")
def admin_users_list(request: Request, db: Session = Depends(get_db)):
    user = require_superuser(request, db)

    users = db.query(AdminUser).order_by(AdminUser.email).all()

    result = []
    for u in users:
        tenant_links = (
            db.query(AdminUserTenant, Tenant)
            .join(Tenant, Tenant.id == AdminUserTenant.tenant_id)
            .filter(AdminUserTenant.user_id == u.id)
            .all()
        )

        perms = (
            db.query(AdminUserPermission.permission_key)
            .filter(AdminUserPermission.user_id == u.id)
            .all()
        )

        result.append({
            "id": u.id,
            "email": u.email,
            "is_active": u.is_active,
            "is_superuser": u.is_superuser,
            "tenants": [
                {"id": t.id, "key": t.key, "name": t.name}
                for _, t in tenant_links
            ],
            "permissions": [p[0] for p in perms]
        })

    return result

@app.post("/admin/users")
def admin_create_user(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    user = require_superuser(request, db)

    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    is_superuser = bool(payload.get("is_superuser", False))

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    existing = db.query(AdminUser).filter(AdminUser.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    new_user = AdminUser(
        email=email,
        password_hash=hash_password(password),
        is_active=True,
        is_superuser=is_superuser
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"ok": True, "id": new_user.id}

@app.post("/admin/users/{user_id}/access")
def admin_update_user_access(
    user_id: int,
    request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db)
):
    user = require_superuser(request, db)

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    tenant_ids = payload.get("tenant_ids") or []
    permissions = payload.get("permissions") or []

    db.query(AdminUserTenant).filter(AdminUserTenant.user_id == user_id).delete()
    db.query(AdminUserPermission).filter(AdminUserPermission.user_id == user_id).delete()

    for tenant_id in tenant_ids:
        db.add(AdminUserTenant(user_id=user_id, tenant_id=int(tenant_id)))

    for perm in permissions:
        db.add(AdminUserPermission(user_id=user_id, permission_key=str(perm)))

    db.commit()
    return {"ok": True}

@app.get("/admin/permission-keys")
def admin_permission_keys(request: Request, db: Session = Depends(get_db)):
    require_superuser(request, db)
    return [{"key": key, "label": key} for key in ADMIN_PERMISSION_KEYS]