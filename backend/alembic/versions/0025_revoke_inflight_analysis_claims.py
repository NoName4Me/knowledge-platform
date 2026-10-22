"""Allow workers to converge queued analysis after access revocation.

Revision ID: 0025_revoke_inflight_claims
Revises: 0024_v5_accounts
Create Date: 2026-10-22
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0025_revoke_inflight_claims"
down_revision: str | None = "0024_v5_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError(f"non-identifier RLS role: {ROLE_NAME}")


def upgrade() -> None:
    # V5 must not schedule work for revoked identities, but filtering it out of
    # the claim query leaves already queued rows stuck forever. Claim the work
    # once; the worker's membership check then fails it before reading sources
    # or calling a model.
    op.execute(
        """
        create or replace function app_claim_analysis_document(lease_seconds integer)
        returns table(
          document_execution_id uuid,
          task_id uuid,
          team_id uuid,
          owner_id uuid,
          document_id uuid,
          lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select execution.id
            from analysis_documents execution
            join analysis_tasks task on task.id = execution.task_id
            where task.status in ('queued', 'running')
              and task.stage in ('queued', 'extracting')
              and execution.status = 'queued'
              and execution.attempts < execution.max_attempts
              and not exists (
                select 1
                from analysis_documents prior
                where prior.task_id = execution.task_id
                  and prior.ordinal < execution.ordinal
                  and prior.status in ('queued', 'running')
              )
            order by task.created_at, task.id, execution.ordinal
            for update of execution skip locked
            limit 1
          )
          update analysis_documents target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              started_at = coalesce(target.started_at, now()),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.task_id, target.team_id, target.owner_id,
                    target.document_id, target.lease_token
        $$
        """
    )
    op.execute(
        """
        create or replace function app_claim_analysis_summary(lease_seconds integer)
        returns table(
          summary_id uuid,
          task_id uuid,
          team_id uuid,
          owner_id uuid,
          lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select summary.id
            from analysis_summaries summary
            join analysis_tasks task on task.id = summary.task_id
            where task.kind = 'comparison'
              and task.status = 'running'
              and task.stage = 'comparing'
              and summary.status = 'queued'
              and summary.attempts < summary.max_attempts
            order by task.created_at, task.id
            for update of summary skip locked
            limit 1
          )
          update analysis_summaries target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              started_at = coalesce(target.started_at, now()),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.task_id, target.team_id, target.owner_id,
                    target.lease_token
        $$
        """
    )
    op.execute(
        """
        create or replace function app_claim_knowledge_automation(lease_seconds integer)
        returns table(
          automation_id uuid, team_id uuid, owner_id uuid, lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select automation.id
            from knowledge_automations automation
            where automation.status in ('queued', 'running')
              and automation.stage <> 'canceling'
              and automation.attempts < automation.max_attempts
              and not exists (
                select 1
                from analysis_tasks task
                where task.id = automation.analysis_id
                  and task.status in ('queued', 'running')
              )
              and (
                automation.status = 'queued'
                or (
                  automation.status = 'running'
                  and automation.lease_token is not null
                  and automation.lease_expires_at < now()
                )
              )
            order by automation.created_at, automation.id
            for update skip locked
            limit 1
          )
          update knowledge_automations target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              started_at = coalesce(target.started_at, now()),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.team_id, target.created_by_id, target.lease_token
        $$
        """
    )
    for signature in (
        "app_claim_analysis_document(integer)",
        "app_claim_analysis_summary(integer)",
        "app_claim_knowledge_automation(integer)",
    ):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {ROLE_NAME}")


def downgrade() -> None:
    # Reapply the V5 filtering versions from revision 0024.
    op.execute(
        """
        create or replace function app_claim_analysis_document(lease_seconds integer)
        returns table(
          document_execution_id uuid,
          task_id uuid,
          team_id uuid,
          owner_id uuid,
          document_id uuid,
          lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select execution.id
            from analysis_documents execution
            join analysis_tasks task on task.id = execution.task_id
            join teams team on team.id = execution.team_id
            join users owner_user on owner_user.id = execution.owner_id
            where task.status in ('queued', 'running')
              and task.stage in ('queued', 'extracting')
              and execution.status = 'queued'
              and execution.attempts < execution.max_attempts
              and team.is_active
              and owner_user.is_active
              and exists (
                select 1
                from team_memberships membership
                where membership.team_id = execution.team_id
                  and membership.user_id = execution.owner_id
              )
              and not exists (
                select 1
                from analysis_documents prior
                where prior.task_id = execution.task_id
                  and prior.ordinal < execution.ordinal
                  and prior.status in ('queued', 'running')
              )
            order by task.created_at, task.id, execution.ordinal
            for update of execution skip locked
            limit 1
          )
          update analysis_documents target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              started_at = coalesce(target.started_at, now()),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.task_id, target.team_id, target.owner_id,
                    target.document_id, target.lease_token
        $$
        """
    )
    op.execute(
        """
        create or replace function app_claim_analysis_summary(lease_seconds integer)
        returns table(
          summary_id uuid,
          task_id uuid,
          team_id uuid,
          owner_id uuid,
          lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select summary.id
            from analysis_summaries summary
            join analysis_tasks task on task.id = summary.task_id
            join teams team on team.id = summary.team_id
            join users owner_user on owner_user.id = summary.owner_id
            where task.kind = 'comparison'
              and task.status = 'running'
              and task.stage = 'comparing'
              and summary.status = 'queued'
              and summary.attempts < summary.max_attempts
              and team.is_active
              and owner_user.is_active
              and exists (
                select 1
                from team_memberships membership
                where membership.team_id = summary.team_id
                  and membership.user_id = summary.owner_id
              )
            order by task.created_at, task.id
            for update of summary skip locked
            limit 1
          )
          update analysis_summaries target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              started_at = coalesce(target.started_at, now()),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.task_id, target.team_id, target.owner_id,
                    target.lease_token
        $$
        """
    )
    op.execute(
        """
        create or replace function app_claim_knowledge_automation(lease_seconds integer)
        returns table(
          automation_id uuid, team_id uuid, owner_id uuid, lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select automation.id
            from knowledge_automations automation
            join teams team on team.id = automation.team_id
            join users owner_user on owner_user.id = automation.created_by_id
            where automation.status in ('queued', 'running')
              and automation.stage <> 'canceling'
              and automation.attempts < automation.max_attempts
              and team.is_active
              and owner_user.is_active
              and exists (
                select 1
                from team_memberships membership
                where membership.team_id = automation.team_id
                  and membership.user_id = automation.created_by_id
              )
              and not exists (
                select 1
                from analysis_tasks task
                where task.id = automation.analysis_id
                  and task.status in ('queued', 'running')
              )
              and (
                automation.status = 'queued'
                or (
                  automation.status = 'running'
                  and automation.lease_token is not null
                  and automation.lease_expires_at < now()
                )
              )
            order by automation.created_at, automation.id
            for update skip locked
            limit 1
          )
          update knowledge_automations target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              started_at = coalesce(target.started_at, now()),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.team_id, target.created_by_id, target.lease_token
        $$
        """
    )
    for signature in (
        "app_claim_analysis_document(integer)",
        "app_claim_analysis_summary(integer)",
        "app_claim_knowledge_automation(integer)",
    ):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {ROLE_NAME}")
