"""
Re-index all unindexed documents in the database.

Usage:
    python scripts/index_documents.py
    python scripts/index_documents.py --all   # re-index everything, even already indexed
"""
import asyncio
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from db.session import AsyncSessionLocal, init_db
from db.models import Document
from services.ingestion_service import IngestionService


async def index_documents(reindex_all: bool = False) -> None:
    print("Initialising database...")
    await init_db()

    async with AsyncSessionLocal() as session:
        query = select(Document)
        if not reindex_all:
            query = query.where(Document.is_indexed == False)

        result = await session.execute(query)
        documents = list(result.scalars().all())

        if not documents:
            print("No documents to index.")
            return

        print(f"Found {len(documents)} document(s) to index...")
        service = IngestionService(session)

        for i, doc in enumerate(documents, 1):
            print(f"  [{i}/{len(documents)}] Indexing: {doc.original_filename}")
            try:
                if reindex_all:
                    await service.reindex_document(doc.id)
                else:
                    await service.index_document(doc)
                print(f"    ✅ Done")
            except Exception as e:
                print(f"    ❌ Failed: {e}")

        await session.commit()
        print(f"\nIndexing complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Index documents into the RAG pipeline")
    parser.add_argument("--all", action="store_true", help="Re-index all documents")
    args = parser.parse_args()

    asyncio.run(index_documents(reindex_all=args.all))


if __name__ == "__main__":
    main()
