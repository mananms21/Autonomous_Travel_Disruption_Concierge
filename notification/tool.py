from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()


class NotificationTool:
    """
    LangGraph Tool wrapper around the Notification Service.
    """

    def __init__(self):
        self.base_url = os.getenv(
            "notification_URL",
            "http://127.0.0.1:8001",
        ).rstrip("/")

    async def notify(self, event: dict) -> dict:
        """
        Send an event to the notification service.
        """

        async with httpx.AsyncClient(timeout=30) as client:

            response = await client.post(
                f"{self.base_url}/notify",
                json=event,
            )

            response.raise_for_status()

            return response.json()

    async def health(self) -> bool:
        """
        Check whether the notification service is running.
        """

        try:
            async with httpx.AsyncClient(timeout=5) as client:

                response = await client.get(
                    f"{self.base_url}/docs"
                )

                return response.status_code == 200

        except Exception:
            return False