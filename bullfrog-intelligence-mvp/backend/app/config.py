from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    database_url: str = "sqlite:///./bullfrog_ai.db"

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""

    revio_base_url: str = ""
    revio_api_key: str = ""

    webex_access_token: str = ""
    webex_org_id: str = ""

    ccwr_base_url: str = ""
    ccwr_client_id: str = ""
    ccwr_client_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
