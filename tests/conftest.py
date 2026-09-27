import os
import tempfile

_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_db}",
        "PAYPAL_MODE": "sandbox",
        "PAYPAL_CLIENT_ID": "test-client",
        "PAYPAL_CLIENT_SECRET": "test-secret",
        "PAYPAL_WEBHOOK_ID": "WH-TEST",
        "AZURE_TENANT_ID": "11111111-1111-1111-1111-111111111111",
        "AZURE_API_AUDIENCE": "api://donations-test",
        "AZURE_ADMIN_ROLE": "Donations.Admin",
        "KEY_VAULT_URL": "",
    }
)
