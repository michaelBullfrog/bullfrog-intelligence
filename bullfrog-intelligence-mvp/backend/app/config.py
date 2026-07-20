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

    # Rev.io legacy SOAP API. The service URL is tenant/environment specific,
    # so configure it explicitly rather than guessing a production endpoint.
    revio_soap_url: str = ""
    revio_soap_username: str = ""
    revio_soap_password: str = ""
    revio_soap_client_code: str = ""
    revio_soap_timeout_seconds: int = 45
    revio_soap_verify_ssl: bool = True
    revio_transactions_use_soap: bool = False

    webex_access_token: str = ""
    webex_org_id: str = ""

    # Cisco Commerce / CCW-R
    cisco_us_client_id: str = ""
    cisco_us_client_secret: str = ""
    cisco_canada_client_id: str = ""
    cisco_canada_client_secret: str = ""
    cisco_token_url: str = (
        "https://id.cisco.com/oauth2/default/v1/token"
    )
    cisco_commerce_api_url: str = (
        "https://capi.cisco.com/commerce/apis"
    )
    cisco_request_timeout_seconds: int = 60
    cisco_graphql_timeout_seconds: int = 120
    cisco_verify_ssl: bool = True
    cisco_token_refresh_buffer_seconds: int = 120
    cisco_max_page_size: int = 100
    cisco_default_lookback_days: int = 365
    cisco_max_lookback_days: int = 3650
    cisco_window_days: int = 15
    cisco_max_pages_per_window: int = 20
    cisco_max_interactive_records: int = 5000

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
    def revio_soap_configured(self) -> bool:
        has_explicit_credentials = bool(
            self.revio_soap_username
            and self.revio_soap_password
            and self.revio_soap_client_code
        )
        has_reusable_basic_auth = bool(
            self.revio_billing_authorization
            and self.revio_billing_authorization.lower().startswith("basic ")
        )
        return bool(
            self.revio_soap_url
            and (has_explicit_credentials or has_reusable_basic_auth)
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
