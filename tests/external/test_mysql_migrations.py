from sqlalchemy import inspect


async def test_mysql_migration_created_expected_tables(mysql_database):
    async with mysql_database.engine.connect() as connection:
        def inspect_schema(sync_connection):
            inspector = inspect(sync_connection)
            return (
                set(inspector.get_table_names()),
                {
                    column["name"]
                    for column in inspector.get_columns("tool_calls")
                },
            )

        table_names, tool_call_columns = await connection.run_sync(inspect_schema)

    assert {
        "alembic_version",
        "conversations",
        "messages",
        "agent_runs",
        "tool_calls",
    } <= table_names
    assert {
        "requires_approval",
        "approval_status",
        "approval_request_hash",
        "approval_requested_at",
        "approval_expires_at",
        "approval_decided_at",
        "approval_decided_by",
        "approval_reason",
    } <= tool_call_columns
