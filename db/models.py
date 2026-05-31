from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from core.config import settings

EMBEDDING_DIM = settings.EMBED_DIMENSION


class Base(DeclarativeBase):
    pass


class Role(Base):
    """
    A team / department / access tier. Replaces the old hardcoded
    {hr, developer} string column.

    `persona_name`, `domain_label`, `scope_description`, and
    `general_knowledge_scope` are the four labeled slots that role-admins
    fill in via the UI. The prompt builder substitutes them into the
    system-controlled template — role-admins can't rewrite the safety
    scaffold, only customise the persona.
    """
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    persona_name: Mapped[str] = mapped_column(String(50), nullable=False)
    domain_label: Mapped[str] = mapped_column(String(50), nullable=False)
    scope_description: Mapped[str] = mapped_column(Text, nullable=False)
    general_knowledge_scope: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    memberships: Mapped[list["UserRole"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )
    document_assignments: Mapped[list["DocumentRole"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Role id={self.id} name={self.name!r}>"


class UserRole(Base):
    """
    Many-to-many user ↔ role association.

    `is_role_admin` flags whether this membership grants admin-level
    privileges within that role — i.e. the right to add/remove members
    of this role, upload/edit/delete documents tagged with this role,
    and edit this role's persona. It is scoped to one role only; a
    user can be a role-admin of HR and a regular member of Engineering.
    Superusers (`User.is_superuser=True`) bypass these flags everywhere.

    The conversation-privacy invariant: nothing about `is_role_admin`
    grants visibility into other users' conversations or messages. That
    is enforced at the query layer (ownership joins on user_id), not by
    role membership.
    """
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    is_role_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="role_memberships")
    role: Mapped["Role"] = relationship(back_populates="memberships")

    def __repr__(self) -> str:
        return (
            f"<UserRole user_id={self.user_id} role_id={self.role_id} "
            f"admin={self.is_role_admin}>"
        )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # Global superuser: bypasses every RBAC check. Role-scoped admin lives
    # on UserRole.is_role_admin.
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    role_memberships: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    # ── Convenience accessors (computed, never persisted) ─────────────
    @property
    def role_ids(self) -> list[int]:
        """IDs of every role this user is a member of."""
        return [m.role_id for m in self.role_memberships]

    @property
    def admin_role_ids(self) -> list[int]:
        """IDs of roles this user is a *role-admin* of."""
        return [m.role_id for m in self.role_memberships if m.is_role_admin]

    def is_member_of(self, role_id: int) -> bool:
        return any(m.role_id == role_id for m in self.role_memberships)

    def is_admin_of(self, role_id: int) -> bool:
        """True iff this user is an admin of `role_id`, or a superuser."""
        if self.is_superuser:
            return True
        return any(
            m.role_id == role_id and m.is_role_admin for m in self.role_memberships
        )

    def __repr__(self) -> str:
        return (
            f"<User id={self.id} username={self.username!r} "
            f"superuser={self.is_superuser}>"
        )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/pdf")
    uploaded_by_admin: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_indexed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    role_assignments: Mapped[list["DocumentRole"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.filename!r}>"


class DocumentRole(Base):
    """
    Which roles are permitted to retrieve a given document.

    Replaces the old varchar role column with a real FK so renaming a
    role doesn't desync the access table from `roles`.
    """
    __tablename__ = "document_roles"
    __table_args__ = (
        UniqueConstraint("document_id", "role_id", name="uq_document_role_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="role_assignments")
    role: Mapped["Role"] = relationship(back_populates="document_assignments")

    def __repr__(self) -> str:
        return f"<DocumentRole doc_id={self.document_id} role_id={self.role_id}>"


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    document: Mapped["Document"] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<DocumentChunk id={self.id} doc_id={self.document_id} idx={self.chunk_index}>"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    def __repr__(self) -> str:
        return f"<Conversation id={self.id} user_id={self.user_id}>"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"<Message id={self.id} role={self.role!r}>"


class RevokedToken(Base):
    """
    Denylist of consumed / revoked refresh-token jti values.

    A jti lands here when (a) the user logs out, or (b) a refresh token is
    rotated (its old jti is consumed). Presenting a jti that's already here
    means either a logged-out token or a replayed old token — both are
    rejected. `expires_at` lets a periodic job purge entries once the
    underlying token would have expired anyway.
    """
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RevokedToken jti={self.jti[:8]}… user={self.user_id}>"
