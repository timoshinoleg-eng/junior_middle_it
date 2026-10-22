"""Allow destructive Copilot tests only on the local copilot_test database."""
from psycopg.conninfo import conninfo_to_dict

LOCAL_TEST_HOSTS = frozenset(("localhost", "127.0.0.1", "::1"))
TEST_DATABASE = "copilot_test"


def validate_test_dsn(dsn: str) -> None:
    """Reject every other target before connecting, without logging credentials."""
    try:
        settings = conninfo_to_dict(dsn) if dsn and dsn.strip() else {}
    except Exception:
        raise ValueError("Invalid PostgreSQL test DSN; require local host and database copilot_test") from None
    database = settings.get("dbname", "")
    host = settings.get("host", "")
    hostaddr = settings.get("hostaddr", "")
    if database != TEST_DATABASE or host not in LOCAL_TEST_HOSTS or (hostaddr and hostaddr not in LOCAL_TEST_HOSTS):
        raise ValueError("Unsafe PostgreSQL test target: database=%r host=%r hostaddr=%r; require copilot_test on localhost, 127.0.0.1 or ::1" % (database, host, hostaddr))
