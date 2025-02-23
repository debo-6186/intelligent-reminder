from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    twilio_account_sid: str
    twilio_auth_token: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    aws_dynamodb_table: str
    aws_dynamodb_endpoint: str
    aws_dynamodb_region: str
    dynamodb_table: str
    elevenlabs_api_key: str

    model_config = SettingsConfigDict(env_file='.env')

settings = Settings()