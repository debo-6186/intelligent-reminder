from twilio.rest import Client
from config import settings

twilio_client = Client(
    settings.twilio_account_sid,
    settings.twilio_auth_token
)