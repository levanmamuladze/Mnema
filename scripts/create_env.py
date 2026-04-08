"""
Helper to create a .env file from .env.example with a generated SECRET_KEY.

Usage:
    python scripts/create_env.py
"""
import os
import secrets
import sys


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    example_path = os.path.join(root, ".env.example")
    env_path = os.path.join(root, ".env")

    if os.path.exists(env_path):
        print(".env already exists. Delete it first if you want to regenerate.")
        sys.exit(0)

    if not os.path.exists(example_path):
        print(".env.example not found.")
        sys.exit(1)

    with open(example_path) as f:
        content = f.read()

    secret_key = secrets.token_urlsafe(48)
    content = content.replace("CHANGE_ME_USE_32_CHARS_MINIMUM_RANDOM_STRING", secret_key)

    with open(env_path, "w") as f:
        f.write(content)

    print(f"✅ .env created with a generated SECRET_KEY.")
    print(f"   Edit {env_path} to set your DATABASE_URL and other settings.")


if __name__ == "__main__":
    main()
