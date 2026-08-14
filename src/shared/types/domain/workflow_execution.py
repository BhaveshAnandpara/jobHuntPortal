"""WorkflowExecution (the "WorkflowState" entity) — an observability/audit
record of one LangGraph workflow run (one job-matching run, one
contact-discovery run, one outreach-generation run). Distinct from the
typed in-memory LangGraph state used while a workflow executes
(JobMatchingState, etc. — see docs/architecture/langgraph-state.md);
WorkflowExecution is the durable record written *about* that run. See
docs/architecture/domain-model.md#workflowexecution-the-workflowstate-entity.

Ownership: shared, row-level ownership by workflow_type — each component
that runs a LangGraph workflow (Job Matching Service, Contact Discovery
Service, Outreach Service) writes only the rows it creates. See
docs/architecture/database-ownership.md#shared-observability-table.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import WorkflowStatus, WorkflowType
from shared.types.ids import CorrelationId, WorkflowExecutionId


class WorkflowExecution(BaseModel):
    id: WorkflowExecutionId
    workflow_type: WorkflowType
    correlation_id: CorrelationId  # ties this run back to the originating event chain
    entity_ref_id: str  # the JobId/ContactId/OutreachId this run operated on
    status: WorkflowStatus
    current_node: str | None = None  # last node entered
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    retry_count: int = 0
