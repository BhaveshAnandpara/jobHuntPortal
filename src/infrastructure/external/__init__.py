"""External Integrations Layer — Playwright/BeautifulSoup (job-page
extraction), job board search APIs/scrapers, people-search APIs/tools, and
email/LinkedIn send clients. Owned by the External Integrations Agent
(.claude/agents/external-integrations-agent.md). See
docs/architecture/dependency-graph.md#5-llmtool-dependencies and
docs/architecture/ownership.md#infrastructure-ownership-non-business.

Each client is used only by the component(s) documented in
docs/architecture/ownership.md#component--external-toolsapis-it-may-call
(Job Ingestion Service, Job Discovery Service, Contact Discovery Service,
Outreach Service) — this module owns only the reusable adapter/transport
code, never job discovery, contact ranking, or outreach decision logic.

Public surface: see `page_fetch.py` (PageFetchClient), `job_search.py`
(JobBoardSearchClient), `people_search.py` (PeopleSearchClient),
`message_send.py` (MessageSendClient), `resilience.py` (shared timeout/
retry/rate-limit primitives), `errors.py` (normalized exception types),
and `config.py` (transport configuration). No symbols are re-exported at
package level — import from the specific submodule.
"""
