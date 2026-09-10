"""Health/readiness probe router — infrastructure, not a business component.

Owns exactly one public, unauthenticated endpoint:

    GET /health

which reports whether this process can reach its two hard runtime
dependencies (PostgreSQL and Kafka). It is what a reverse proxy, uptime
checker, or container orchestrator polls — see docs/deployment/TECH.md
Task 8 (`curl https://api.<domain>/health` must return 200) plus the
Caddyfile and docker-compose.app.yml, all of which assume the route is
reachable without a token.

This package deliberately contains no business logic and reads no business
table: it only composes the two already-tested connectivity primitives
`infrastructure.database.health.check_connection_async` and
`infrastructure.kafka.health.check_connection`, which until now had no
caller. Mounted into the app in `api/main.py` like every other router.
"""

from infrastructure.health.api import router

__all__ = ["router"]
