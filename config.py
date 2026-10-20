import os
from dotenv import load_dotenv

load_dotenv()

SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
DB_PATH = os.getenv("CLOUDONOMIX_DB", "cloudonomix.db")
HIBERNATE_TAG = os.getenv("AUTOHIBERNATE_TAG", "autohibernate")
DRY_RUN = os.getenv("AUTOHIBERNATE_DRY_RUN", "true").lower() != "false"
IDLE_CPU_THRESHOLD = float(os.getenv("IDLE_CPU_THRESHOLD", "5"))
IDLE_WINDOW_HOURS = int(os.getenv("IDLE_WINDOW_HOURS", "24"))

# AWS: boto3 reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the environment
# (load_dotenv puts the .env values there) or from `aws configure`.
AWS_REGIONS = [r.strip() for r in os.getenv("AWS_REGIONS", "ap-south-1").split(",") if r.strip()]
STOP_TIMEOUT_SECONDS = int(os.getenv("STOP_TIMEOUT_SECONDS", "300"))


def aws_configured():
    """True if AWS keys are in the environment or a shared credentials file exists."""
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        return True
    return os.path.exists(os.path.expanduser("~/.aws/credentials"))


def get_credential():
    """Service principal if all three vars are set, otherwise the `az login` session."""
    from azure.identity import AzureCliCredential, ClientSecretCredential

    tenant = os.getenv("AZURE_TENANT_ID")
    client = os.getenv("AZURE_CLIENT_ID")
    secret = os.getenv("AZURE_CLIENT_SECRET")
    if tenant and client and secret:
        return ClientSecretCredential(tenant, client, secret)
    return AzureCliCredential()
