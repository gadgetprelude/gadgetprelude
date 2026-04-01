import os
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

MS_CLIENT_ID = os.getenv("MS_CLIENT_ID")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET")
MS_TENANT_ID = os.getenv("MS_TENANT_ID", "organizations")
MS_REDIRECT_URI = os.getenv("MS_REDIRECT_URI")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def get_ms_authority_base() -> str:
    tenant = (MS_TENANT_ID or "organizations").strip()
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"


def build_microsoft_auth_url(state: str) -> str:
    if not MS_CLIENT_ID:
        raise RuntimeError("MS_CLIENT_ID is missing")
    if not MS_REDIRECT_URI:
        raise RuntimeError("MS_REDIRECT_URI is missing")

    scope = "openid profile offline_access User.Read Calendars.ReadWrite"
    params = {
        "client_id": MS_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": MS_REDIRECT_URI,
        "response_mode": "query",
        "scope": scope,
        "state": state,
        "prompt": "select_account",
    }
    return f"{get_ms_authority_base()}/authorize?{urlencode(params)}"


def exchange_microsoft_code_for_tokens(code: str) -> dict:
    token_url = f"{get_ms_authority_base()}/token"

    data = {
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": MS_REDIRECT_URI,
        "scope": "openid profile offline_access User.Read Calendars.ReadWrite",
    }

    response = requests.post(token_url, data=data, timeout=30)
    response.raise_for_status()
    return response.json()


def refresh_microsoft_tokens(refresh_token: str) -> dict:
    token_url = f"{get_ms_authority_base()}/token"

    data = {
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "redirect_uri": MS_REDIRECT_URI,
        "scope": "openid profile offline_access User.Read Calendars.ReadWrite",
    }

    response = requests.post(token_url, data=data, timeout=30)
    response.raise_for_status()
    return response.json()


def graph_get_me(access_token: str) -> dict:
    response = requests.get(
        f"{GRAPH_BASE}/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def graph_list_calendars(access_token: str) -> dict:
    response = requests.get(
        f"{GRAPH_BASE}/me/calendars",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def graph_create_event(access_token: str, payload: dict) -> dict:
    response = requests.post(
        f"{GRAPH_BASE}/me/events",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def graph_update_event(access_token: str, event_id: str, payload: dict) -> dict:
    response = requests.patch(
        f"{GRAPH_BASE}/me/events/{event_id}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def graph_delete_event(access_token: str, event_id: str) -> None:
    response = requests.delete(
        f"{GRAPH_BASE}/me/events/{event_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    response.raise_for_status()

def graph_calendar_view(access_token: str, start_at, end_at):
    params = {
        "startDateTime": start_at.isoformat(),
        "endDateTime": end_at.isoformat(),
    }

    response = requests.get(
        f"{GRAPH_BASE}/me/calendarView",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()