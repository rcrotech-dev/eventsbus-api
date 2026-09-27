# Eventsbus API

FastAPI + PayPal Orders v2 donation service with Azure AD–protected admin API, deployable to Azure App Service (or Container Apps via the Dockerfile).

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /` | public | Donation page (PayPal Smart Buttons) |
| `GET /api/config` | public | PayPal client ID, currency, limits |
| `POST /api/donations/orders` | public | Create PayPal order + pending donation |
| `POST /api/donations/orders/{id}/capture` | public | Capture approved order |
| `POST /api/paypal/webhook` | PayPal signature | Capture completed/denied/refunded/reversed |
| `GET /api/donations` | Azure AD, role `Donations.Admin` | List donations |

## 1. Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 (docs at `/docs`).

## 2. PayPal sandbox

1. https://developer.paypal.com/dashboard/applications → **Sandbox** → create app → copy Client ID / Secret into `.env`.
2. Webhooks need a public URL. Locally: `ngrok http 8000` (or a VS Code dev tunnel), then add webhook `https://<tunnel>/api/paypal/webhook` with events `PAYMENT.CAPTURE.COMPLETED`, `.DENIED`, `.PENDING`, `.REFUNDED`, `.REVERSED`. Copy the **Webhook ID** into `PAYPAL_WEBHOOK_ID`.
3. Donate on the page using a sandbox *personal* account (Dashboard → Testing Tools → Sandbox Accounts).

## 3. Deploy to Azure App Service

```bash
RG=rg-donations; LOC=eastus; APP=eventsbus-api-<unique>; PLAN=plan-donations
az group create -n $RG -l $LOC
az appservice plan create -g $RG -n $PLAN --is-linux --sku B1
az webapp create -g $RG -p $PLAN -n $APP --runtime "PYTHON:3.12"
az webapp config set -g $RG -n $APP \
  --startup-file "gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000"
az webapp config appsettings set -g $RG -n $APP --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true ENVIRONMENT=production PAYPAL_MODE=sandbox \
  DATABASE_URL="sqlite:////home/data/donations.db" \
  PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... PAYPAL_WEBHOOK_ID=...
az webapp update -g $RG -n $APP --https-only true
```

For production, use Azure Database for PostgreSQL: add `psycopg[binary]` to `requirements.txt` and set `DATABASE_URL=postgresql+psycopg://...`.

### GitHub Actions (`.github/workflows/deploy.yml`)

Uses OIDC (no stored Azure secret):

```bash
az ad app create --display-name gh-donations-deploy      # note appId
az ad sp create --id <appId>
az role assignment create --assignee <appId> --role "Website Contributor" \
  --scope $(az webapp show -g $RG -n $APP --query id -o tsv)
az ad app federated-credential create --id <appId> --parameters '{
  "name":"gh-main","issuer":"https://token.actions.githubusercontent.com",
  "subject":"repo:<owner>/<repo>:environment:production","audiences":["api://AzureADTokenExchange"]}'
```

Repo secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. Repo variable: `AZURE_WEBAPP_NAME`. Create a `production` environment.

## 4. Azure AD authentication (admin API)

1. Entra ID → App registrations → **New** (`eventsbus-api`). Note the Application (client) ID.
2. **Expose an API** → set Application ID URI `api://<client-id>`.
3. **App roles** → create `Donations.Admin` (allowed member types: Users/Groups + Applications).
4. Enterprise applications → `eventsbus-api` → Users and groups → assign admins.
5. App settings: `AZURE_TENANT_ID=<tenant>`, `AZURE_API_AUDIENCE=<client-id>,api://<client-id>`.

Test: `az account get-access-token --resource api://<client-id>` (add Azure CLI `04b07795-8ddb-461a-bbee-02f9e1bf7b46` as an authorized client app under Expose an API), then `curl -H "Authorization: Bearer <token>" https://$APP.azurewebsites.net/api/donations`.

Validation checks: RS256 signature against tenant JWKS, `aud`, `iss` (v1 + v2), `exp`/`nbf`, `tid`, and the `roles` claim (see `tests/test_auth.py`).

## 5. Key Vault for secrets

```bash
KV=kv-donations-<unique>
az keyvault create -g $RG -n $KV --enable-rbac-authorization true
az webapp identity assign -g $RG -n $APP                        # note principalId
az role assignment create --assignee <principalId> --role "Key Vault Secrets User" \
  --scope $(az keyvault show -n $KV --query id -o tsv)
az keyvault secret set --vault-name $KV -n paypal-client-id     --value ...
az keyvault secret set --vault-name $KV -n paypal-client-secret --value ...
az keyvault secret set --vault-name $KV -n paypal-webhook-id    --value ...
az keyvault secret set --vault-name $KV -n database-url         --value ...   # optional
az webapp config appsettings set -g $RG -n $APP --settings KEY_VAULT_URL=https://$KV.vault.azure.net/
az webapp config appsettings delete -g $RG -n $APP --setting-names PAYPAL_CLIENT_ID PAYPAL_CLIENT_SECRET PAYPAL_WEBHOOK_ID
```

The app loads those secrets at startup via managed identity (`app/config.py`). Alternative with no code: App Service Key Vault references, e.g. `PAYPAL_CLIENT_SECRET=@Microsoft.KeyVault(SecretUri=https://$KV.vault.azure.net/secrets/paypal-client-secret/)`.

## 6. Switch PayPal to live

1. developer.paypal.com → **Live** → create app → live Client ID / Secret.
2. Create a live webhook → `https://$APP.azurewebsites.net/api/paypal/webhook`, same events → new Webhook ID.
3. Update the three Key Vault secrets with live values; set `PAYPAL_MODE=live`; restart the app (`az webapp restart`).
4. Make a small real donation, confirm `COMPLETED` in `/api/donations`, and refund it from the PayPal dashboard to verify the refund webhook.

## Container Apps (optional)

```bash
az acr build -r <acr> -t eventsbus-api:latest .
az containerapp up -n eventsbus-api -g $RG --image <acr>.azurecr.io/eventsbus-api:latest --target-port 8000 --ingress external
```
