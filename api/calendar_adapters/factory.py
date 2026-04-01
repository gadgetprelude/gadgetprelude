from fastapi import HTTPException
from .google_adapter import GoogleCalendarAdapter
from .microsoft_adapter import MicrosoftCalendarAdapter


def get_calendar_adapter(provider_name: str):
    key = (provider_name or "").strip().lower()

    if key == "google":
        return GoogleCalendarAdapter()

    if key == "microsoft":
        return MicrosoftCalendarAdapter()

    raise HTTPException(status_code=400, detail=f"Unsupported calendar provider: {provider_name}")