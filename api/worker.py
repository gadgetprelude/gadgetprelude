import time
from datetime import datetime, timezone

from db import SessionLocal
from models import Reminder, Appointment, Contact, Tenant
from main import send_email_reminder, build_customer_reminder_email
from dotenv import load_dotenv

load_dotenv()

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
                tenant = db.get(Tenant, ap.tenant_id)

                if (not contact) or (not contact.email):
                    r.status = "failed"
                    r.error = "Contact has no email"
                    db.add(r)
                    db.commit()
                    continue

                try:
                    subject, html = build_customer_reminder_email(ap, db)

                    send_email_reminder(
                        contact.email,
                        subject,
                        html,
                        tenant_name=tenant.name if tenant else None
                    )

                    r.status = "sent"
                    r.error = None
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