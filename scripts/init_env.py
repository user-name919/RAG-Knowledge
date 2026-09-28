"""Create private local settings without displaying secrets or overwriting a file."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / '.env'
if target.exists():
    print('.env already exists; left unchanged.')
else:
    content = (root / '.env.example').read_text()
    content = content.replace('replace-with-random-root-password', secrets.token_hex(24))
    content = content.replace('replace-with-random-app-password', secrets.token_hex(24))
    content = content.replace('replace-with-random-admin-key', secrets.token_urlsafe(32))
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(content)
    print('Created .env (0600). Fill EMBEDDING_API_KEY locally; secrets were not printed.')
