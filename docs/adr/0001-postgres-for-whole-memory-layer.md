# Postgres for the whole Cognee memory layer

The product already locked AvalAI, Cognee, Postgres, and Docker/VPS. Cognee documents `GRAPH_DATABASE_PROVIDER=postgres_demo` as demo-not-production and recommends Kuzu or Neo4j for the graph in production. We still keep **the whole memory layer on Postgres forever** (relational metadata, PGVector, session cache, and graph). Phase 2 uses `postgres_demo`. The only allowed later upgrade is Cognee’s licensed Postgres graph adapter — still Postgres. Kuzu and Neo4j are out.

## Considered options

- Split the graph onto Kuzu or Neo4j (Cognee’s production default). Rejected: breaks the Postgres-only stack and adds a second database we cannot operate before the 10 September 2026 Session.
- Wait for / buy the licensed production Postgres adapter before the Session. Rejected for Phase 2 timeline; it remains the only upgrade path after.
- Accept `postgres_demo` for Phase 2 only, revisit the graph store later. Rejected by the PM: Postgres only, permanently.

## Consequences

- `SearchType.CYPHER` and `NATURAL_LANGUAGE` stay unsupported on this backend. Next-tier search stays `GRAPH_COMPLETION_COT`, which does not need Cypher.
- Cognee’s demo warning is an accepted risk, not a reason to add another database.
