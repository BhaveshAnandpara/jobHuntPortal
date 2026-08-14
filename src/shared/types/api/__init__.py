"""API request/response contracts, one module per owning service. See
docs/architecture/api-contracts.md.

Naming: every request type ends in `Request`, every response type ends in
`Response`. A response type's fields never introduce a new representation
of an entity already defined in domain-model.md — it's either that entity's
fields verbatim or an explicit named subset.
"""
