"""
RBAC isolation — the product's core security guarantee.

These tests prove, against a real Postgres + pgvector database, that document
retrieval is filtered by role *in the query*: a user can only ever receive
chunks from documents tagged with one of their roles. The decisive case is
`test_role_filter_beats_similarity`, which makes the FORBIDDEN chunk the single
closest vector match and asserts the user still never receives it — i.e. the
access boundary wins over similarity, exactly as the marketing claim states.

This is an integration test: it needs a Postgres instance with the `vector`
extension. It is skipped unless TEST_DATABASE_URL is set, e.g.

    TEST_DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app pytest -k rbac

CI provides this via a pgvector service container (see .github/workflows/ci.yml).
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import settings
from db.models import (
    Base,
    Document,
    DocumentRole,
    Role,
    User,
)
from db.repositories.document_repository import DocumentChunkRepository

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (RBAC isolation needs Postgres + pgvector)",
)

DIM = settings.EMBED_DIMENSION


def _unit_vector(hot_index: int) -> list[float]:
    """A length-DIM vector that is 1.0 at `hot_index` and 0.0 elsewhere.

    Two such vectors with different hot indexes are orthogonal (cosine
    distance 1.0); identical ones have cosine distance 0.0. That lets the
    tests place chunks at precise, deterministic distances from a query.
    """
    vec = [0.0] * DIM
    vec[hot_index] = 1.0
    return vec


@pytest_asyncio.fixture
async def session():
    """Fresh schema per test against the real Postgres+pgvector instance."""
    engine = create_async_engine(TEST_DATABASE_URL, future=True)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def world(session):
    """Two roles, two documents (one per role), one chunk each.

    - Role A's chunk sits at distance 1.0 from the query (orthogonal).
    - Role B's chunk sits at distance 0.0 from the query (identical) — i.e. it
      is the single best similarity match in the whole corpus.

    The query embedding equals Role B's chunk embedding, so any retrieval that
    ignored RBAC would return Role B's chunk first.
    """
    uploader = User(
        username="uploader",
        email="uploader@example.com",
        hashed_password="x",
        is_superuser=True,
    )
    session.add(uploader)
    await session.flush()

    def make_role(name: str) -> Role:
        return Role(
            name=name,
            display_name=name.title(),
            persona_name=name.title(),
            domain_label=name,
            scope_description=f"{name} scope",
            general_knowledge_scope=f"{name} general",
        )

    role_a = make_role("alpha")
    role_b = make_role("bravo")
    session.add_all([role_a, role_b])
    await session.flush()

    def make_doc(label: str) -> Document:
        return Document(
            filename=f"{label}.txt",
            original_filename=f"{label}.txt",
            file_path=f"/tmp/{label}.txt",
            file_size=10,
            mime_type="text/plain",
            uploaded_by_admin=uploader.id,
            is_indexed=True,
        )

    doc_a = make_doc("alpha-doc")
    doc_b = make_doc("bravo-doc")
    session.add_all([doc_a, doc_b])
    await session.flush()

    session.add_all([
        DocumentRole(document_id=doc_a.id, role_id=role_a.id),
        DocumentRole(document_id=doc_b.id, role_id=role_b.id),
    ])
    await session.flush()

    chunk_repo = DocumentChunkRepository(session)
    # Role A chunk: orthogonal to the query (distance 1.0).
    await chunk_repo.create_chunk(
        document_id=doc_a.id,
        content="ALPHA SECRET",
        chunk_index=0,
        embedding=_unit_vector(1),
    )
    # Role B chunk: identical to the query (distance 0.0 — the best match).
    await chunk_repo.create_chunk(
        document_id=doc_b.id,
        content="BRAVO SECRET",
        chunk_index=0,
        embedding=_unit_vector(0),
    )
    await session.commit()

    return {
        "query": _unit_vector(0),  # equals Role B's embedding
        "role_a": role_a,
        "role_b": role_b,
        "doc_a": doc_a,
        "doc_b": doc_b,
    }


async def test_role_filter_beats_similarity(session, world):
    """A Role A user must never receive Role B's chunk — even though that
    chunk is the single closest vector match to the query."""
    repo = DocumentChunkRepository(session)
    rows = await repo.get_top_chunks_for_roles(
        query_embedding=world["query"],
        role_ids=[world["role_a"].id],
        top_k=10,
    )
    returned_doc_ids = {chunk.document_id for _, chunk in rows}

    assert returned_doc_ids == {world["doc_a"].id}, (
        "Role A retrieval leaked or dropped documents"
    )
    assert world["doc_b"].id not in returned_doc_ids, (
        "ISOLATION BREACH: Role A received Role B's document"
    )
    assert all("BRAVO" not in chunk.content for _, chunk in rows)


async def test_each_role_sees_only_its_own(session, world):
    repo = DocumentChunkRepository(session)

    a_rows = await repo.get_top_chunks_for_roles(
        query_embedding=world["query"], role_ids=[world["role_a"].id], top_k=10
    )
    b_rows = await repo.get_top_chunks_for_roles(
        query_embedding=world["query"], role_ids=[world["role_b"].id], top_k=10
    )

    assert {c.document_id for _, c in a_rows} == {world["doc_a"].id}
    assert {c.document_id for _, c in b_rows} == {world["doc_b"].id}


async def test_multi_role_user_sees_union(session, world):
    repo = DocumentChunkRepository(session)
    rows = await repo.get_top_chunks_for_roles(
        query_embedding=world["query"],
        role_ids=[world["role_a"].id, world["role_b"].id],
        top_k=10,
    )
    assert {c.document_id for _, c in rows} == {world["doc_a"].id, world["doc_b"].id}


async def test_no_roles_sees_nothing(session, world):
    """Fail-closed: a user with zero role memberships retrieves nothing."""
    repo = DocumentChunkRepository(session)
    rows = await repo.get_top_chunks_for_roles(
        query_embedding=world["query"], role_ids=[], top_k=10
    )
    assert rows == []


async def test_superuser_sees_everything(session, world):
    repo = DocumentChunkRepository(session)
    rows = await repo.get_top_chunks_superuser(
        query_embedding=world["query"], top_k=10
    )
    assert {c.document_id for _, c in rows} == {world["doc_a"].id, world["doc_b"].id}
    # The closest match (distance 0.0) must be Role B's chunk.
    nearest_distance, nearest_chunk = rows[0]
    assert nearest_chunk.document_id == world["doc_b"].id
    assert nearest_distance == pytest.approx(0.0, abs=1e-6)
