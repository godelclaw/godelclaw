# Provider SQL reference

These unchanged source snapshots preserve the independent policy, schema,
and reader comparison used before SQLite retirement. The manifest identifies
their source commit and SHA-256 hashes. `sqlite_provider_reference.py` verifies
the snapshots and binds their dependencies under separate module identities.

They run only inside disposable test databases. They are excluded from runtime
releases and provide no fallback from the agent's native ledger.
