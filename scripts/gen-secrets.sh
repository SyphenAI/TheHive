#!/usr/bin/env sh
# Prints a ready-to-paste .env secrets block.
python3 - <<'PY'
import secrets
from cryptography.fernet import Fernet
print(f"SECRET_KEY={secrets.token_urlsafe(48)}")
print(f"TOKEN_FERNET_KEY={Fernet.generate_key().decode()}")
PY
