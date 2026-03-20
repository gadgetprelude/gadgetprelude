import time
from datetime import datetime, timezone

from db import SessionLocal
from models import Reminder, Appointment, Contact, Service

def send_email_stub(to_email: str, subject: str, body: str):
    # MVP: stub (imprime no terminal). Depois ligamos SMTP/SendGrid.
    print(
        f"\n--- EMAIL (stub) ---\n"
        f"To: {to_email}\n"
        f"Subject: {subject}\n\n"
        f"{body}\n"
        f"-------------------\n"
    )

def run_loop(poll_seconds: int = 10):
    print("Worker de lembretes a correr... (CTRL+C para parar)")
    while True:
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)

            due = (
                db.query(Reminder)
                .filter(Reminder.status == "pending", Reminder.send_at <= now)
                .order_by(Reminder.send_at.asc())
                .limit(25)
                .all()
            )

            for r in due:
                ap = db.get(Appointment, r.appointment_id)
                if (not ap) or (ap.status == "cancelled"):
                    r.status = "cancelled"
                    db.add(r)
                    db.commit()
                    continue

                contact = db.get(Contact, ap.contact_id)
                service = db.get(Service, ap.service_id)

                if (not contact) or (not contact.email):
                    r.status = "failed"
                    r.error = "Contact has no email"
                    db.add(r)
                    db.commit()
                    continue

                subject = f"Lembrete: {service.name if service else 'Marcação'}"
                body = (
                    f"Olá {contact.name},\n\n"
                    f"Isto é um lembrete da sua marcação.\n"
                    f"Quando: {ap.start_at.isoformat()}\n\n"
                    f"Até já,\n"
                    f"GadgetPrelude"
                )

                try:
                    send_email_stub(contact.email, subject, body)
                    r.status = "sent"
                    db.add(r)
                    db.commit()
                except Exception as e:
                    r.status = "failed"
                    r.error = str(e)
                    db.add(r)
                    db.commit()

        finally:
            db.close()

        time.sleep(poll_seconds)

if __name__ == "__main__":
    run_loop()
