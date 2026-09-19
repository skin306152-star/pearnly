"""Infrastructure credential check. No tenant, user document, OCR charge or inference."""
from google.auth.transport.requests import Request
from google.oauth2 import service_account

credentials = service_account.Credentials.from_service_account_file(
    "/run/secrets/ocr.json", scopes=["https://www.googleapis.com/auth/cloud-platform"])
credentials.refresh(Request())
assert credentials.valid and credentials.token
print("PASS cloud OCR service-account authentication; token suppressed; no model inference")
