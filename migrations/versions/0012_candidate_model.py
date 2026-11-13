"""candidate model revisions (ADR 0009): about-me, fact origin and inclusion, removed fields

Revision ID: 0012_candidate_model
Revises: 0011_llm_task_models
Create Date: 2026-11-13
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_candidate_model"
down_revision: str | None = "0011_llm_task_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

ABOUT_ME_MAX = 2000
ORIGINS = ("manual", "api", "import", "document_question")
KINDS_AFTER = ("resume", "certificate", "transcript", "portfolio", "profile", "reference", "other")
KINDS_BEFORE = (*KINDS_AFTER[:-1], "user_statement", "other")
KIND_CONSTRAINT = "ck_evidence_sources_evidence_kind"
ORIGIN_CONSTRAINT = "ck_candidate_facts_fact_origin"
# (fact type, attribute) pairs nothing reads any more.
REMOVED_ATTRIBUTES = (("skill", "proficiency"), ("experience", "employment_type"))


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    conn = op.get_bind()
    too_long = conn.execute(
        sa.text("SELECT count(*) FROM candidates WHERE char_length(summary) > :n"),
        {"n": ABOUT_ME_MAX},
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            f"{too_long} candidate(s) have a summary longer than {ABOUT_ME_MAX} characters. "
            "It becomes the about-me, which is limited to that length. Shorten it with "
            "PATCH /api/v1/candidates/<id> before upgrading; nothing was changed."
        )
    op.alter_column(
        "candidates",
        "summary",
        new_column_name="about_me",
        type_=sa.String(length=ABOUT_ME_MAX),
        existing_type=sa.Text(),
        existing_nullable=True,
    )

    # Existing facts' origin cannot be told apart reliably; the database is cleared after
    # this change (ADR 0009), so every existing row is "api".
    op.add_column(
        "candidate_facts",
        sa.Column("origin", sa.String(length=32), nullable=False, server_default="api"),
    )
    op.alter_column("candidate_facts", "origin", server_default=None)
    op.create_check_constraint(op.f(ORIGIN_CONSTRAINT), "candidate_facts", _in("origin", ORIGINS))
    op.add_column(
        "candidate_facts",
        sa.Column("include_in_documents", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    for fact_type, key in REMOVED_ATTRIBUTES:
        stripped = conn.execute(
            sa.text(
                "UPDATE candidate_facts SET attributes = attributes - :key "
                "WHERE fact_type = :type AND attributes ? :key"
            ),
            {"key": key, "type": fact_type},
        ).rowcount
        logger.info("removed %s from %d %s fact(s)", key, stripped, fact_type)

    converted = conn.execute(
        sa.text("UPDATE evidence_sources SET kind = 'other' WHERE kind = 'user_statement'")
    ).rowcount
    logger.info("changed %d user_statement evidence source(s) to other", converted)
    op.drop_constraint(op.f(KIND_CONSTRAINT), "evidence_sources", type_="check")
    op.create_check_constraint(op.f(KIND_CONSTRAINT), "evidence_sources", _in("kind", KINDS_AFTER))


def downgrade() -> None:
    # Stripped attributes and converted evidence kinds are not restored.
    op.drop_constraint(op.f(KIND_CONSTRAINT), "evidence_sources", type_="check")
    op.create_check_constraint(op.f(KIND_CONSTRAINT), "evidence_sources", _in("kind", KINDS_BEFORE))
    op.drop_column("candidate_facts", "include_in_documents")
    op.drop_constraint(op.f(ORIGIN_CONSTRAINT), "candidate_facts", type_="check")
    op.drop_column("candidate_facts", "origin")
    op.alter_column(
        "candidates",
        "about_me",
        new_column_name="summary",
        type_=sa.Text(),
        existing_type=sa.String(length=ABOUT_ME_MAX),
        existing_nullable=True,
    )
