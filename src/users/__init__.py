"""User Service — account identity and search preferences.

Ownership: `User`, `UserPreferences` (docs/architecture/domain-model.md).
Owned tables: `users`, `user_preferences`
(docs/architecture/database-ownership.md).
APIs: `/users`, `/users/{id}/preferences`
(docs/architecture/api-contracts.md#user-service).
Kafka: none produced, none consumed.
Dependencies: none (docs/architecture/service-boundaries.md#user-service).

Explicitly outside this component's responsibility: anything about *what*
the user is professionally (Resume/Profile Service) or *which* jobs they've
seen (Tracking Service).
"""
