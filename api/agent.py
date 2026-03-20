import json
from typing import Any, Dict, List, Optional
from datetime import datetime

from openai import OpenAI
from sqlalchemy.orm import Session

from models import Tenant, Contact, Service, Appointment, CalendarConnection

client = OpenAI()  # lê OPENAI_API_KEY do ambiente


# ---------- TOOLS (funções internas) ----------

def tool_find_contact(db: Session, tenant_id: int, name: str) -> Dict[str, Any]:
    q = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant_id)
        .filter(Contact.name.ilike(f"%{name}%"))
        .order_by(Contact.id.desc())
        .limit(5)
        .all()
    )
    return {
        "matches": [{"id": c.id, "name": c.name, "email": c.email, "phone": c.phone} for c in q]
    }

def tool_find_service(db: Session, tenant_id: int, name: str) -> Dict[str, Any]:
    q = (
        db.query(Service)
        .filter(Service.tenant_id == tenant_id)
        .filter(Service.name.ilike(f"%{name}%"))
        .order_by(Service.id.desc())
        .limit(5)
        .all()
    )
    return {
        "matches": [{"id": s.id, "name": s.name, "duration_minutes": s.duration_minutes} for s in q]
    }

def tool_create_appointment(db: Session, tenant_id: int, calendar_email: str, contact_id: int, service_id: int, start_at_iso: str, description: str = "") -> Dict[str, Any]:
    # valida tenant do contact/service
    c = db.get(Contact, contact_id)
    s = db.get(Service, service_id)
    if (not c) or c.tenant_id != tenant_id:
        return {"ok": False, "error": "Contact not found in tenant"}
    if (not s) or s.tenant_id != tenant_id:
        return {"ok": False, "error": "Service not found in tenant"}

    conn = (
        db.query(CalendarConnection)
        .filter(CalendarConnection.tenant_id == tenant_id, CalendarConnection.email == calendar_email)
        .first()
    )
    if not conn:
        return {"ok": False, "error": "No Google Calendar connection for that email in this tenant"}

    # reutilizamos o teu endpoint logic? aqui fazemos simples: chamamos a mesma lógica do main refatorável depois
    # Para não depender de FastAPI Depends, fazemos criação manual do Appointment chamando a lógica já existente no main mais tarde.
    # Por agora, devolvemos erro a pedir refactor se necessário.
    try:
        # Import local, evita circular se refatorares
        from datetime import timedelta
        from main import creds_from_token_json, calendar_service_from_creds, schedule_reminders_for_appointment

        start_at = datetime.fromisoformat(start_at_iso)
        end_at = start_at + timedelta(minutes=int(s.duration_minutes))

        creds = creds_from_token_json(conn.token_json)
        service_api = calendar_service_from_creds(creds)

        event_body = {
            "summary": f"{s.name} - {c.name}",
            "description": description or "",
            "start": {"dateTime": start_at.isoformat()},
            "end": {"dateTime": end_at.isoformat()},
        }
        created = service_api.events().insert(calendarId=conn.calendar_id, body=event_body).execute()

        ap = Appointment(
            tenant_id=tenant_id,
            contact_id=c.id,
            service_id=s.id,
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
            "ok": True,
            "appointment_id": ap.id,
            "google_event_id": ap.external_event_id,
            "google_link": ap.external_html_link,
            "start_at": ap.start_at.isoformat(),
            "end_at": ap.end_at.isoformat(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------- TOOL SCHEMAS (para o modelo) ----------

TOOLS = [
    {
        "type": "function",
        "name": "find_contact",
        "description": "Procura contactos existentes no tenant por nome (devolve até 5).",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "required": ["name"]
        },
    },
    {
        "type": "function",
        "name": "find_service",
        "description": "Procura serviços existentes no tenant por nome (devolve até 5).",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "required": ["name"]
        },
    },
    {
        "type": "function",
        "name": "create_appointment",
        "description": "Cria uma marcação (só se contact/service já existirem).",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_email": {"type": "string"},
                "contact_id": {"type": "integer"},
                "service_id": {"type": "integer"},
                "start_at_iso": {"type": "string", "description": "ISO 8601 com timezone, ex: 2025-12-26T15:00:00+00:00"},
                "description": {"type": "string"},
            },
            "required": ["calendar_email", "contact_id", "service_id", "start_at_iso"]
        },
    },
]


def run_agent(db: Session, tenant: Tenant, user_text: str) -> Dict[str, Any]:
    instructions = f"""
És um assistente para marcações do produto GadgetPrelude.
Tenant atual: {tenant.key} (id={tenant.id})

Regras:
- NÃO cries contactos nem serviços.
- Se o contacto não existir, pergunta ao utilizador para o criar primeiro.
- Se o serviço não existir, pergunta ao utilizador para o criar primeiro.
- Se faltar data/hora, pergunta.
- Usa tools para procurar contactos/serviços e criar a marcação.
- Quando criares, responde com confirmação + link do Google.
"""

    resp = client.responses.create(
        model="gpt-5.2",
        instructions=instructions,
        tools=TOOLS,
        input=user_text,
    )

    # Loop simples: enquanto houver tool calls, executa e volta a enviar outputs
    tool_outputs: List[Dict[str, Any]] = []

    # A SDK devolve output estruturado; aqui tratamos de forma defensiva
    # Procuramos itens do tipo tool_call
    def extract_tool_calls(response_obj) -> List[Dict[str, Any]]:
        calls = []
        for item in getattr(response_obj, "output", []) or []:
            if isinstance(item, dict) and item.get("type") in ("tool_call", "function_call"):
                calls.append(item)
        return calls

    calls = extract_tool_calls(resp)

    while calls:
        tool_outputs = []
        for call in calls:
            name = call.get("name") or call.get("function", {}).get("name")
            args_json = call.get("arguments") or call.get("function", {}).get("arguments") or "{}"
            args = json.loads(args_json) if isinstance(args_json, str) else (args_json or {})

            if name == "find_contact":
                out = tool_find_contact(db, tenant.id, args["name"])
            elif name == "find_service":
                out = tool_find_service(db, tenant.id, args["name"])
            elif name == "create_appointment":
                out = tool_create_appointment(
                    db,
                    tenant.id,
                    args["calendar_email"],
                    int(args["contact_id"]),
                    int(args["service_id"]),
                    args["start_at_iso"],
                    args.get("description", "") or "",
                )
            else:
                out = {"ok": False, "error": f"Unknown tool: {name}"}

            tool_outputs.append({
                "type": "function_call_output",
                "call_id": call.get("id"),
                "output": json.dumps(out),
            })

        resp = client.responses.create(
            model="gpt-5.2",
            instructions=instructions,
            tools=TOOLS,
            input=[{"role": "user", "content": user_text}] + [{"role": "tool", "content": o["output"]} for o in tool_outputs],
        )
        calls = extract_tool_calls(resp)

    # Texto final
    final_text = getattr(resp, "output_text", None) or "OK"
    return {"text": final_text}
