from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

KEY_VAULT_SECRETS = {
    "paypal_client_id": "paypal-client-id",
    "paypal_client_secret": "paypal-client-secret",
    "paypal_webhook_id": "paypal-webhook-id",
    "database_url": "database-url",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Eventsbus API"
    environment: str = "local"
    database_url: str = "sqlite:///./donations.db"

    paypal_mode: Literal["sandbox", "live"] = "sandbox"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_webhook_id: str = ""

    donation_currency: str = "USD"
    donation_min: Decimal = Decimal("1.00")
    donation_max: Decimal = Decimal("10000.00")

    azure_tenant_id: str = ""
    azure_api_audience: str = ""  # comma-separated, e.g. "<client-id>,api://<client-id>"
    azure_admin_role: str = "Donations.Admin"

    key_vault_url: str = ""

    @property
    def paypal_base_url(self) -> str:
        if self.paypal_mode == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def audiences(self) -> list[str]:
        return [a.strip() for a in self.azure_api_audience.split(",") if a.strip()]


def _load_key_vault_secrets(settings: Settings) -> Settings:
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=settings.key_vault_url, credential=DefaultAzureCredential())
    updates = {}
    for field, secret_name in KEY_VAULT_SECRETS.items():
        try:
            updates[field] = client.get_secret(secret_name).value
        except ResourceNotFoundError:
            continue
    return settings.model_copy(update=updates)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.key_vault_url:
        settings = _load_key_vault_secrets(settings)
    return settings
