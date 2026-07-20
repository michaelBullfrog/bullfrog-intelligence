from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    database_url: str = "sqlite:///./bullfrog_ai.db"

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""

    # Rev.io PSA
    revio_base_url: str = "https://api.psarev.io"
    revio_api_key: str = ""
    revio_host: str = "bullfrog.psarev.io"
    revio_token_exchange_path: str = "/api/v1/auth/api-key/exchange"
    revio_ticket_list_path: str = "/psac/api/v1/ticket-list"
    revio_request_timeout_seconds: int = 30
    revio_verify_ssl: bool = True
    revio_token_refresh_buffer_seconds: int = 60

    # Rev.io Billing REST API
    revio_billing_base_url: str = "https://restapi.rev.io"
    revio_billing_authorization: str = ""
    revio_billing_timeout_seconds: int = 30
    revio_billing_verify_ssl: bool = True
    revio_billing_subscription_key: str = ""

    webex_access_token: str = ""
    webex_org_id: str = ""

    ccwr_base_url: str = ""
    ccwr_client_id: str = ""
    ccwr_client_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def revio_configured(self) -> bool:
        return bool(
            self.revio_base_url
            and self.revio_api_key
            and self.revio_host
        )

    @property
    def revio_billing_configured(self) -> bool:
        return bool(
            self.revio_billing_base_url
            and self.revio_billing_authorization
        )

    def revio_url(self, path: str) -> str:
        return (
            f"{self.revio_base_url.rstrip('/')}/"
            f"{path.lstrip('/')}"
        )


settings = Settings()
