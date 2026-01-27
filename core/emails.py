import requests
from decouple import config
import logging

logger = logging.getLogger(__name__)

BREVO_API_KEY = config("BREVO_API_KEY")
BREVO_URL = "https://api.brevo.com/v3/smtp/email"

DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL")
SENDER_NAME = "Gold Privilege"


def send_email(
    subject: str,
    to_email: str,
    message: str,
    html_message: str | None = None,
):
    """
    Brevo API email sender (replacement for send_mail)
    """

    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }

    payload = {
        "sender": {
            "name": SENDER_NAME,
            "email": DEFAULT_FROM_EMAIL.split("<")[-1].replace(">", ""),
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": message,
    }

    if html_message:
        payload["htmlContent"] = html_message

    try:
        response = requests.post(
            BREVO_URL,
            headers=headers,
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        return True

    except Exception as e:
        logger.error(f"Brevo email failed: {e}")
        return False
