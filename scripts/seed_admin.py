"""
Run once to create the initial admin user.

Usage:
    python scripts/seed_admin.py
    python scripts/seed_admin.py --username admin --email admin@company.com --password Admin123!
"""
import asyncio
import argparse
import sys
import os

# Make sure we can import from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.session import AsyncSessionLocal, init_db
from db.repositories.user_repository import UserRepository
from core.exceptions import ConflictError


async def seed_admin(username: str, email: str, password: str) -> None:
    print("Initialising database...")
    await init_db()

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)

        try:
            user = await repo.create_user(
                username=username,
                email=email,
                plain_password=password,
                is_superuser=True,
            )
            await session.commit()
            print(f"\n✅ Superuser created successfully!")
            print(f"   Username : {user.username}")
            print(f"   Email    : {user.email}")
            print(f"   ID       : {user.id}")
            print(f"\nYou can now log in at http://localhost:8000/api/docs")
        except ConflictError as e:
            print(f"\n⚠️  {e.detail}")
            print("Admin user already exists. No changes made.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed initial admin user")
    parser.add_argument("--username", default="admin", help="Admin username")
    parser.add_argument("--email", default="admin@company.com", help="Admin email")
    parser.add_argument("--password", default="Admin123!", help="Admin password")
    args = parser.parse_args()

    asyncio.run(seed_admin(args.username, args.email, args.password))


if __name__ == "__main__":
    main()
