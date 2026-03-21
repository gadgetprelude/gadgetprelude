import os
import json
import requests
from pydantic import BaseModel
from agent import run_agent
from fastapi import Header
from models import Tenant
from models import ReminderPolicy, Reminder  
from pydantic import BaseModel, EmailStr
from datetime import datetime
from models import CalendarConnection, Contact, Service, Appointment, Provider, ProviderService
from datetime import datetime, date, time, timedelta, timezone
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from dotenv import load_dotenv
from itsdangerous import URLSafeSerializer
from sqlalchemy.orm import Session
from datetime import timedelta
from models import Reminder
from db import Base, engine, get_db
from models import CalendarConnection
from google_oauth import build_flow, creds_from_token_json, calendar_service_from_creds
from pathlib import Path
from typing import List
from fastapi.middleware.cors import CORSMiddleware


load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

app = FastAPI(title="gadgetprelude API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    calendar_email: str
    service_id: int
    provider_id: int
    start_at: datetime
    customer_name: str
    customer_email: str

# cria tabelas (MVP). Em produção: migrations (alembic).
Base.metadata.create_all(bind=engine)

serializer = URLSafeSerializer(os.getenv("SESSION_SECRET", "dev"))

@app.get("/")
def home():
    return {"ok": True, "service": "gadgetprelude", "time": datetime.now(timezone.utc).isoformat()}

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

@app.get("/auth/google/callback")
def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    try:
        data = serializer.loads(state)

        tenant_key = (data.get("tenant_key") or "default").strip()

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

    existing = db.query(CalendarConnection).filter(CalendarConnection.tenant_id == tenant.id, CalendarConnection.email == email).first()
    if existing:
    	existing.token_json = token_json
    	existing.calendar_id = calendar_id
    	db.add(existing)
    else:
    	conn = CalendarConnection(tenant_id=tenant.id, email=email, calendar_id=calendar_id, token_json=token_json)
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
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if provider.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Provider not in tenant")
    
    service = db.get(Service, payload.service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # criar contacto automaticamente
    contact = Contact(
        tenant_id=tenant.id,
        name=payload.customer_name,
        email=payload.customer_email
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)

    # ligação ao google
    conn = db.query(CalendarConnection).filter(
        CalendarConnection.tenant_id == tenant.id,
        CalendarConnection.email == payload.calendar_email
    ).first()

    creds = creds_from_token_json(conn.token_json)
    service_api = calendar_service_from_creds(creds)

    start = payload.start_at
    end = start + timedelta(minutes=service.duration_minutes)

    event_body = {
        "summary": f"{service.name} - {payload.customer_name} - {provider.name}",
        "description": "Booking via GadgetPrelude",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "attendees": [
            {"email": payload.customer_email}
        ]
    }

    created = service_api.events().insert(
        calendarId=conn.calendar_id,
        body=event_body,
        sendUpdates="all"
    ).execute()

    ap = Appointment(
        tenant_id=tenant.id,
        contact_id=contact.id,
        provider_id=provider.id,
        service_id=service.id,
        start_at=start,
        end_at=end,
        status="scheduled",
        external_event_id=created.get("id"),
        external_html_link=created.get("htmlLink")
    )

    db.add(ap)
    db.commit()

    return {
        "ok": True,
        "message": "Booking criado com sucesso",
        "google_link": created.get("htmlLink")
    }

def to_utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

@app.get("/public/availability")
def public_availability(
    service_id: int,
    date_str: str,
    tenant_key: str = "default",
    provider_id: int | None = None,
    db: Session = Depends(get_db),
):
    tenant = get_tenant(db, tenant_key)

    service = db.get(Service, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    if service.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Service not in tenant")

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format")

    day_start = datetime.combine(target_date, time(9, 0), tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time(18, 0), tzinfo=timezone.utc)

    query = (
        db.query(Appointment)
        .filter(Appointment.tenant_id == tenant.id)
        .filter(Appointment.status.in_(["scheduled", "rescheduled"]))
        .filter(Appointment.start_at < day_end)
        .filter(Appointment.end_at > day_start)
    )

    if provider_id is not None:
        query = query.filter(Appointment.provider_id == provider_id)

    existing = query.order_by(Appointment.start_at.asc()).all()

    slots = []
    current = day_start
    slot_step = timedelta(minutes=30)
    service_duration = timedelta(minutes=service.duration_minutes)

    while current + service_duration <= day_end:
        candidate_end = current + service_duration

        overlaps = any(
            to_utc_aware(ap.start_at) < candidate_end and to_utc_aware(ap.end_at) > current
            for ap in existing
        )

        if not overlaps:
            slots.append(current.isoformat())

        current += slot_step

    return {
        "service_id": service.id,
        "date": target_date.isoformat(),
        "duration_minutes": service.duration_minutes,
        "slots": slots,
    }

@app.get("/public/services")
def public_services(
    tenant_key: str = "default",
    provider_id: int | None = None,
    db: Session = Depends(get_db),
):
    tenant = get_tenant(db, tenant_key)

    query = (
        db.query(Service)
        .filter(Service.tenant_id == tenant.id)
    )

    if provider_id is not None:
        query = (
            query.join(ProviderService, ProviderService.service_id == Service.id)
            .filter(ProviderService.tenant_id == tenant.id)
            .filter(ProviderService.provider_id == provider_id)
        )

    services = query.all()

    return [
        {
            "id": s.id,
            "name": s.name,
            "duration_minutes": s.duration_minutes,
        }
        for s in services
    ]

@app.get("/public/config")
def public_config(tenant_key: str = "default", db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)

    conn = (
        db.query(CalendarConnection)
        .filter(CalendarConnection.tenant_id == tenant.id)
        .first()
    )

    return {
        "tenant_key": tenant.key,
        "business_name": tenant.name,
        "calendar_email": conn.email if conn else None,
        "primary_color": "#2563eb",
        "subtitle": "Escolhe o serviço, a data e o horário que te for mais conveniente.",
        "success_message": "Marcação criada com sucesso. Verifica o teu email para o convite."
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
def public_providers(tenant_key: str = "default", db: Session = Depends(get_db)):
    tenant = get_tenant(db, tenant_key)

    providers = (
        db.query(Provider)
        .filter(Provider.tenant_id == tenant.id)
        .all()
    )

    return [
        {
            "id": p.id,
            "name": p.name,
        }
        for p in providers
    ]