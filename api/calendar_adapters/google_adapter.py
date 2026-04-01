from datetime import datetime
from fastapi.responses import RedirectResponse

from google_oauth import build_flow, creds_from_token_json, calendar_service_from_creds, google_freebusy
from .base import CalendarAdapterBase


class GoogleCalendarAdapter(CalendarAdapterBase):
    provider_name = "google"

    def build_auth_start_response(self, tenant_key: str, provider_id: int, serializer):
        state = serializer.dumps({
            "ts": datetime.utcnow().isoformat(),
            "tenant_key": tenant_key,
            "provider_id": provider_id,
            "calendar_provider": "google"
        })

        flow = build_flow(state=state)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return RedirectResponse(auth_url)

    def test_connection(self, connection):
        creds = creds_from_token_json(connection.token_json)
        service = calendar_service_from_creds(creds)
        return service.calendarList().get(calendarId=connection.calendar_id).execute()

    def create_event(
        self,
        connection,
        provider,
        service_obj,
        customer_name: str,
        customer_email: str,
        start_at,
        end_at,
    ):
        creds = creds_from_token_json(connection.token_json)
        service_api = calendar_service_from_creds(creds)

        event_body = {
            "summary": f"{service_obj.name} - {customer_name} - {provider.name}",
            "description": "Booking via GadgetPrelude",
            "start": {"dateTime": start_at.isoformat()},
            "end": {"dateTime": end_at.isoformat()},
            "attendees": [{"email": customer_email}],
        }

        created = service_api.events().insert(
            calendarId=connection.calendar_id,
            body=event_body,
            sendUpdates="all"
        ).execute()

        return {
            "external_event_id": created.get("id"),
            "external_html_link": created.get("htmlLink"),
            "raw": created,
        }

    def update_event(
        self,
        connection,
        event_id: str,
        start_at,
        end_at,
    ):
        creds = creds_from_token_json(connection.token_json)
        service_api = calendar_service_from_creds(creds)

        updated = service_api.events().patch(
            calendarId=connection.calendar_id,
            eventId=event_id,
            body={
                "start": {"dateTime": start_at.isoformat()},
                "end": {"dateTime": end_at.isoformat()},
            }
        ).execute()

        return {
            "external_html_link": updated.get("htmlLink"),
            "raw": updated,
        }

    def delete_event(
        self,
        connection,
        event_id: str,
    ):
        creds = creds_from_token_json(connection.token_json)
        service_api = calendar_service_from_creds(creds)

        service_api.events().delete(
            calendarId=connection.calendar_id,
            eventId=event_id
        ).execute()

        return {"ok": True}
    def handle_callback(self, code: str):
        raise NotImplementedError("Google callback ainda está tratado diretamente no main.py")

    def get_busy_intervals(self, connection, start_at, end_at):
        creds = creds_from_token_json(connection.token_json)
        result = google_freebusy(
            creds=creds,
            calendar_id=connection.calendar_id,
            time_min=start_at,
            time_max=end_at,
        )

        busy = (
            result.get("calendars", {})
            .get(connection.calendar_id, {})
            .get("busy", [])
        )

        return busy