from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    database_url: str = "sqlite:///./bullfrog_ai.db"

    revio_base_url: str = "https://api.psarev.io"
    revio_api_key: str = ""
    revio_host: str = "bullfrog.psarev.io"
    revio_token_exchange_path: str = "/api/v1/auth/api-key/exchange"
    revio_ticket_list_path: str = "/psac/api/v1/ticket-list"
    revio_request_timeout_seconds: float = 30
    revio_verify_ssl: bool = True
    revio_token_refresh_buffer_seconds: int = 60

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""
    webex_access_token: str = ""
    webex_org_id: str = ""
    ccwr_base_url: str = ""
    ccwr_client_id: str = ""
    ccwr_client_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def revio_configured(self) -> bool:
        return bool(self.revio_base_url.strip() and self.revio_api_key.strip() and self.revio_host.strip())

    def revio_url(self, path: str) -> str:
        return self.revio_base_url.rstrip("/") + "/" + path.lstrip("/")

settings = Settings()
