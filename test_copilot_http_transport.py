"""The credit system over the real HTTP transport of the Edge bridges.

The bridges of the product do not talk to PostgreSQL directly: they forward
one query per request to an Edge function, and the Edge function turns every
error of PostgreSQL into an HTTP 503 with the body ``{"error": "query
failed"}``. The SQLSTATE of the error never reaches the store, so a missing
table cannot be told apart from any other failure over this transport.

This module runs the real statements of the store through a real HTTP server
that answers with the contract of the Edge function, against a real
PostgreSQL. It proves that the credit system fails closed when the registry
of the operations is missing: no credit is taken, no journal entry is
written, and no statement of the old journal is sent as a fallback.
"""
import json
import os
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from copilot.store import CopilotStore

DSN_ENV = "COPILOT_TEST_PG_DSN"
USER_ID = 5252
REF = "job-http-transport"

SCHEMA = """
CREATE TABLE IF NOT EXISTS public.growth_copilot_credits (
    user_id BIGINT PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    free_granted_on DATE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS public.growth_copilot_credit_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS public.growth_copilot_credit_ops (
    user_id BIGINT NOT NULL,
    ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'reserved',
    amount INTEGER NOT NULL DEFAULT 1,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, ref, kind)
);
"""


_PLACEHOLDER_RE = re.compile(r"\$\d+")


def _to_psycopg(query: str, params):
    """The placeholders of the bridge are the ones of postgres.js.

    The client of the bridge sends the query with the placeholders of
    postgres.js, and the driver of the test speaks the placeholders of
    psycopg. The values keep their order.
    """
    return _PLACEHOLDER_RE.sub("%s", str(query)), list(params or ())


class EdgeLikeHandler(BaseHTTPRequestHandler):
    """The contract of the Edge function of the growth bridge.

    The function answers with the rows of the query, and it turns every error
    of PostgreSQL into an HTTP 503 with the body ``{"error": "query
    failed"}``. The SQLSTATE is not part of the answer.
    """

    def log_message(self, fmt, *args):
        return

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._send(400, {"error": "invalid json"})
            return
        query = str(payload.get("query") or "")
        params = payload.get("params") or []
        self.server.queries.append((query, list(params)))
        import psycopg

        converted, values = _to_psycopg(query, params)
        try:
            with psycopg.connect(self.server.dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(converted, values)
                    rows = cur.fetchall()
                    columns = [str(column[0]) for column in cur.description or []]
        except Exception:
            # This is what the Edge function does with an error of
            # PostgreSQL: a 503 and a body without the SQLSTATE.
            self.server.errors += 1
            self._send(503, {"error": "query failed"})
            return
        self._send(
            200,
            {
                "rows": [list(row) for row in rows],
                "columns": columns,
                "rowcount": len(rows),
            },
        )


class EdgeLikeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, dsn):
        super().__init__(("127.0.0.1", 0), EdgeLikeHandler)
        self.dsn = dsn
        self.queries = []
        self.errors = 0


class _RemoteStore:
    """The store interface of the product, backed by the remote connection.

    In production this is ``growth_store.PostgresGrowthStore`` with the
    transport of the bridge. The transport itself - the connection and the
    cursor - is the code of the product, only the endpoint points at the
    local server of the test.
    """

    def __init__(self, connection):
        self.conn = connection

    def _ensure_conn(self):
        return self.conn

    def close(self):
        self.conn.close()


def _store(server):
    """The store over the real HTTP transport of the bridge."""
    import http_growth_patch as bridge

    connection = bridge.RemoteGrowthConnection(
        "https://bridge.supabase.co/functions/v1/growth-proxy", "test-key"
    )
    connection.url = "http://127.0.0.1:%d/growth-proxy" % server.server_address[1]
    return CopilotStore(_RemoteStore(connection))


def _reset(dsn, *, with_registry=True):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute("DELETE FROM growth_copilot_credit_events")
            cur.execute("DELETE FROM growth_copilot_credit_ops")
            cur.execute("DELETE FROM growth_copilot_credits")
            cur.execute(
                "INSERT INTO growth_copilot_credits (user_id, balance) VALUES (%s, %s)",
                (USER_ID, 5),
            )
            if not with_registry:
                cur.execute("DROP TABLE IF EXISTS growth_copilot_credit_ops")


def _balance(dsn):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT balance FROM growth_copilot_credits WHERE user_id = %s", (USER_ID,)
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0


def _journal(dsn):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reason, delta FROM growth_copilot_credit_events "
                "WHERE user_id = %s ORDER BY id",
                (USER_ID,),
            )
            return [(row[0], int(row[1])) for row in cur.fetchall()]


class HttpTransportTests(unittest.TestCase):
    """The credit system over the transport that the product uses."""

    def setUp(self):
        self.dsn = os.environ.get(DSN_ENV)
        if not self.dsn:
            self.skipTest("%s is not set: no PostgreSQL for the transport check" % DSN_ENV)
        self.server = EdgeLikeServer(self.dsn)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_a_reservation_over_the_http_transport_charges_once(self):
        """The statements of the store work over the real transport."""
        _reset(self.dsn)
        store = _store(self.server)
        attempt = store.reserve_credit(
            USER_ID, 1, "spend", REF, release_reason="spend_release"
        )
        self.assertEqual(attempt, 1)
        self.assertEqual(_balance(self.dsn), 4)
        self.assertEqual(_journal(self.dsn), [("spend", -1)])

    def test_a_missing_registry_fails_closed_over_the_http_transport(self):
        """A missing table is a deployment mistake, not a reason to charge.

        The bridge answers with an HTTP 503 and without the SQLSTATE, so the
        store cannot tell a missing table from any other failure. The credit
        system therefore stops: no credit is taken, no journal entry is
        written, and no statement of the old journal is sent as a fallback.
        """
        _reset(self.dsn, with_registry=False)
        store = _store(self.server)
        with self.assertRaises(Exception):
            store.reserve_credit(
                USER_ID, 1, "spend", REF, release_reason="spend_release"
            )
        # No credit was taken and nothing was written.
        self.assertEqual(_balance(self.dsn), 5)
        self.assertEqual(_journal(self.dsn), [])
        # The bridge saw the error of PostgreSQL and answered with a 503.
        self.assertGreaterEqual(self.server.errors, 1)
        # No statement of the old journal was sent: there is no fallback.
        # Every statement that writes the journal writes the registry in the
        # same statement, so the registry stays the gate of the credit.
        for query, _params in self.server.queries:
            if "INSERT INTO growth_copilot_credit_events" in query:
                self.assertIn("growth_copilot_credit_ops", query)
            self.assertNotIn("spend_release", query)

    def test_the_answer_of_the_bridge_carries_no_sqlstate(self):
        """The reason why the code cannot guess a missing table.

        The Edge function answers with an HTTP 503 and a body that names the
        error, but the SQLSTATE of PostgreSQL never reaches the store. A
        fallback that watches for the SQLSTATE would never run over the
        transport of the product, so the code must not promise one.
        """
        _reset(self.dsn, with_registry=False)
        store = _store(self.server)
        with self.assertRaises(Exception) as caught:
            store.reserve_credit(
                USER_ID, 1, "spend", REF, release_reason="spend_release"
            )
        error = caught.exception
        self.assertIsNone(getattr(error, "sqlstate", None))
        self.assertNotIn("42P01", str(error))
        self.assertIn("503", str(error))


if __name__ == "__main__":
    unittest.main()
