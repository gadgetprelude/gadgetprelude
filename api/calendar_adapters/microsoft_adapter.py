import json
from datetime import datetime

from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from microsoft_oauth import graph_calendar_view

from microsoft_oauth import (
    build_microsoft_auth_url,
    exchange_microsoft_code_for_tokens,
    refresh_microsoft_tokens,
    graph_get_me,
    graph_list_calendars,
    graph_create_event,
    graph_update_event,
    graph_delete_event,
)
from .base import CalendarAdapterBase


class MicrosoftCalendarAdapter(CalendarAdapterBase):
    provider_name = "microsoft"

    def build_auth_start_response(self, tenant_key: str, provider_id: int, serializer):
        state = serializer.dumps({
            "ts": datetime.utcnow().isoformat(),
            "tenant_key": tenant_key,
            "provider_id": provider_id,
            "calendar_provider": "microsoft"
        })
        auth_url = build_microsoft_auth_url(state=state)
        return RedirectResponse(auth_url)

    def _get_valid_access_token(self, connection):
        try:
            token_data = json.loads(connection.token_json)
        except Exception:
            raise HTTPException(
                status_code=500,
                detail={
                    "ok": False,
                    "status": "invalid_token_storage",
                    "message": "token_json Microsoft inválido."
                }
            )

        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")

        if access_token:
            return access_token, token_data, False

        if not refresh_token:
            raise HTTPException(
                status_code=400,
                detail={
                    "ok": False,
                    "status": "reauth_required",
                    "message": "Ligação Microsoft sem refresh token. É necessário reautenticar."
                }
            )

        try:
            new_token_data = refresh_microsoft_tokens(refresh_token)
            return new_token_data.get("access_token"), new_token_data, True
        except Exception:
            raise HTTPException(
                status_code=400,
                detail={
                    "ok": False,
                    "status": "reauth_required",
                    "message": "Ligação Microsoft expirada ou revogada. É necessário reautenticar."
                }
            )

    def handle_callback(self, code: str):
        token_data = exchange_microsoft_code_for_tokens(code)
        me = graph_get_me(token_data["access_token"])
        calendars = graph_list_calendars(token_data["access_token"])

        default_calendar = None
        for item in calendars.get("value", []):
            if item.get("isDefaultCalendar"):
                default_calendar = item
                break

        return {
            "token_data": token_data,
            "email": me.get("mail") or me.get("userPrincipalName"),
            "calendar_id": default_calendar.get("id") if default_calendar else "primary",
            "profile": me,
            "default_calendar": default_calendar,
        }

    def test_connection(self, connection):
        access_token, new_token_data, refreshed = self._get_valid_access_token(connection)

        if refreshed:
            connection.token_json = json.dumps(new_token_data)

        me = graph_get_me(access_token)
        calendars = graph_list_calendars(access_token)

        default_calendar = None
        for item in calendars.get("value", []):
            if item.get("isDefaultCalendar"):
                default_calendar = item
                break

        return {
            "summary": default_calendar.get("name") if default_calendar else "Default calendar",
            "me": me,
            "default_calendar": default_calendar,
            "token_data": new_token_data if refreshed else None,
        }

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
        access_token, new_token_data, refreshed = self._get_valid_access_token(connection)

        if refreshed:
            connection.token_json = json.dumps(new_token_data)

        payload = {
            "subject": f"{service_obj.name} - {customer_name} - {provider.name}",
            "body": {
                "contentType": "HTML",
                "content": "Booking via GadgetPrelude"
            },
            "start": {
                "dateTime": start_at.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC"
            },
            "end": {
                "dateTime": end_at.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC"
            },
            "attendees": [
                {
                    "emailAddress": {
                        "address": customer_email,
                        "name": customer_name
                    },
                    "type": "required"
                }
            ]
        }

        created = graph_create_event(access_token, payload)

        return {
            "external_event_id": created.get("id"),
            "external_html_link": created.get("webLink"),
            "raw": created,
            "token_data": new_token_data if refreshed else None,
        }

    def update_event(
        self,
        connection,
        event_id: str,
        start_at,
        end_at,
    ):
        access_token, new_token_data, refreshed = self._get_valid_access_token(connection)

        if refreshed:
            connection.token_json = json.dumps(new_token_data)

        payload = {
            "start": {
                "dateTime": start_at.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC"
            },
            "end": {
                "dateTime": end_at.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC"
            }
        }

        updated = graph_update_event(access_token, event_id, payload)

        return {
            "external_html_link": updated.get("webLink") if isinstance(updated, dict) else None,
            "raw": updated,
            "token_data": new_token_data if refreshed else None,
        }

    def delete_event(
        self,
        connection,
        event_id: str,
    ):
        access_token, new_token_data, refreshed = self._get_valid_access_token(connection)

        if refreshed:
            connection.token_json = json.dumps(new_token_data)

        graph_delete_event(access_token, event_id)

        return {
            "ok": True,
            "token_data": new_token_data if refreshed else None,
        }
    
    def get_busy_intervals(self, connection, start_at, end_at):
        access_token, new_token_data, refreshed = self._get_valid_access_token(connection)

        if refreshed:
            connection.token_json = json.dumps(new_token_data)

        data = graph_calendar_view(access_token, start_at, end_at)

        busy = []

        for event in data.get("value", []):
            start = event.get("start", {}).get("dateTime")
            end = event.get("end", {}).get("dateTime")

            if start and end:
                busy.append({
                    "start": start,
                    "end": end,
                })

        return busy