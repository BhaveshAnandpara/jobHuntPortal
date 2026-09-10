"""Health-probe route handler.

Owned endpoint:
    GET /health   (public — no authentication)

Returns 200 while both PostgreSQL and Kafka are reachable, 503 as soon as
either is not, with the same body shape either way so a probe can tell
*which* dependency is down from the failing response alone:

    200 {"status": "ok",        "database": true,  "kafka": true}
    503 {"status": "unhealthy", "database": false, "kafka": true}

`HealthResponse` is defined here rather than in `shared/types/api/`: that
package holds the cross-component *service* contracts of
docs/architecture/api-contracts.md, one module per owning service, and this
probe is infrastructure that no other component reads or calls. Nothing
here is a shared contract, so none is introduced.

Deliberately unauthenticated (no `CurrentUserIdDependency`): a reverse proxy
and uptime checker must be able to poll it — see the Caddyfile and
docker-compose.app.yml, and docs/deployment/TECH.md Task 8. The body
exposes two booleans and no host, credential, or version detail.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from infrastructure.database.health import check_connection_async
from infrastructure.health.dependencies import DatabaseEngineDep, KafkaHealthClientDep
from infrastructure.kafka.health import check_connection as check_kafka_connection

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: bool
    kafka: bool


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def get_health(
    response: Response,
    engine: DatabaseEngineDep,
    kafka_client: KafkaHealthClientDep,
) -> HealthResponse:
    """Probe both runtime dependencies and report them together.

    Neither check raises: `check_connection_async` normalizes every
    `SQLAlchemyError` to `False` and the Kafka check normalizes any broker
    exception the same way, so a down dependency becomes a 503 with a
    truthful body rather than a 500.
    """
    database_ok = await check_connection_async(engine)
    kafka_ok = check_kafka_connection(client=kafka_client)

    healthy = database_ok and kafka_ok
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if healthy else "unhealthy",
        database=database_ok,
        kafka=kafka_ok,
    )


__all__ = ["HealthResponse", "router"]
