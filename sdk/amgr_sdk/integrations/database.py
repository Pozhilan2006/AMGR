from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
import functools
import inspect
import logging
import re
from typing import Any, Iterator, TypeVar
from urllib.parse import urlsplit

from amgr_sdk.capture.recorder import ActiveOperation, EventRecorder, _resolve_data_labels
from amgr_sdk.exceptions import IntegrationError
from amgr_sdk.models.events import ComponentRef, sanitize_metadata

logger = logging.getLogger("amgr_sdk.integrations.database")

F = TypeVar("F", bound=Callable[..., Any])

_COMMENT_REGEX = re.compile(r"(--[^\n]*|\/\*.*?\*\/)", re.DOTALL)
_SQL_OPERATIONS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})


def classify_sql_operation(statement: str | None) -> str:
    """Classify a SQL statement into a standardized safe operation category.

    Extracts the primary operation category: SELECT, INSERT, UPDATE, DELETE, or UNKNOWN.
    Returns 'UNKNOWN' for DDL, non-standard statements, empty, or unparseable inputs.
    Raw SQL text and parameters are never retained.
    """
    if not statement or not isinstance(statement, str):
        return "UNKNOWN"

    cleaned = _COMMENT_REGEX.sub(" ", statement).strip()
    if not cleaned:
        return "UNKNOWN"

    first_token = cleaned.split()[0].upper().strip("();,")
    if first_token in _SQL_OPERATIONS:
        return first_token
    return "UNKNOWN"


def sanitize_database_url(url: str) -> str:
    """Sanitize a database URL or connection string by removing embedded credentials.

    Example:
        'postgresql://admin:secret123@localhost:5432/app_db'
        -> 'localhost:5432/app_db'
    """
    if not url or not isinstance(url, str):
        return "default-database"
    try:
        parts = urlsplit(url)
        if parts.hostname:
            port = f":{parts.port}" if parts.port else ""
            db_path = parts.path.lstrip("/")
            return f"{parts.hostname}{port}/{db_path}" if db_path else f"{parts.hostname}{port}"
        # Fallback for file paths or non-standard URIs (e.g. sqlite:///...)
        cleaned = url.split("@")[-1].split("?")[0].strip()
        return cleaned or "default-database"
    except Exception:
        return url.split("@")[-1].split("?")[0].strip() or "default-database"


def derive_database_ref(
    target: ComponentRef | str | None = None,
    component_id: str | None = None,
    name: str | None = None,
) -> ComponentRef:
    """Resolve a ComponentRef for a database target without embedding credentials."""
    if isinstance(target, ComponentRef):
        return target
    if isinstance(target, str) and target.strip():
        cleaned_id = sanitize_database_url(target)
        return ComponentRef.database(
            component_id=component_id or cleaned_id,
            name=name or cleaned_id,
        )
    return ComponentRef.database(
        component_id=component_id or "default-database",
        name=name or "Database",
    )


def _resolve_recorder(
    client: Any | None = None,
    recorder: EventRecorder | None = None,
) -> EventRecorder | None:
    if recorder is not None:
        return recorder
    if client is not None:
        if isinstance(client, EventRecorder):
            return client
        if hasattr(client, "recorder") and isinstance(client.recorder, EventRecorder):
            return client.recorder
    from amgr_sdk.client import get_default_client

    default_client = get_default_client()
    if default_client is not None and hasattr(default_client, "recorder"):
        return default_client.recorder

    try:
        from amgr_sdk.client import AMGRClient

        return AMGRClient().recorder
    except Exception as exc:
        logger.debug("Could not resolve default EventRecorder for database integration: %s", exc)
        return None


@contextmanager
def db_span(
    operation: str = "UNKNOWN",
    *,
    destination: ComponentRef | str | None = None,
    source: ComponentRef | None = None,
    statement: str | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    status: str = "completed",
) -> Iterator[ActiveOperation]:
    """Context manager for instrumenting database operations.

    Records safe operation categories (SELECT, INSERT, UPDATE, DELETE, UNKNOWN),
    duration, and outcome through the central EventRecorder.
    Raw SQL statements, query parameters, returned rows, and credentials are never captured.
    """
    if statement:
        op_category = classify_sql_operation(statement)
    else:
        op_category = (
            operation.upper()
            if operation.upper() in _SQL_OPERATIONS
            else "UNKNOWN"
        )

    dest = derive_database_ref(destination)
    labels = _resolve_data_labels(data_labels)
    rec = _resolve_recorder(client, recorder)

    safe_meta = {"operation": op_category}
    if metadata:
        safe_meta.update(sanitize_metadata(metadata, redact=True))

    if rec is None:
        from amgr_sdk.transport.base import Transport
        from uuid import uuid4

        class _NoOpTransport(Transport):
            def send(self, event: Any) -> None:
                pass

            def send_batch(self, events: Any) -> None:
                pass

        dummy_op = ActiveOperation(
            recorder=EventRecorder(_NoOpTransport()),
            action="db.query",
            destination=dest,
            source=source or ComponentRef(component_id="unknown-source", component_type="service"),
            data_labels=labels,
            metadata=safe_meta,
            flow_id=uuid4(),
        )
        yield dummy_op
        return

    with rec.start_operation(
        action="db.query",
        destination=dest,
        source=source,
        data_labels=labels,
        metadata=safe_meta,
        enter_context=True,
    ) as op:
        yield op


def capture_db(
    operation: str = "UNKNOWN",
    *,
    destination: ComponentRef | str | None = None,
    source: ComponentRef | None = None,
    statement: str | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    status: str = "completed",
) -> Callable[[F], F]:
    """Decorator to instrument a function or repository method performing database queries."""

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with db_span(
                    operation=operation,
                    destination=destination,
                    source=source,
                    statement=statement,
                    data_labels=data_labels,
                    metadata=metadata,
                    client=client,
                    recorder=recorder,
                    status=status,
                ) as op:
                    result = await func(*args, **kwargs)
                    rowcount = getattr(result, "rowcount", None)
                    if isinstance(rowcount, int) and rowcount >= 0:
                        op.add_metadata("affected_rows", rowcount)
                    return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with db_span(
                operation=operation,
                destination=destination,
                source=source,
                statement=statement,
                data_labels=data_labels,
                metadata=metadata,
                client=client,
                recorder=recorder,
                status=status,
            ) as op:
                result = func(*args, **kwargs)
                rowcount = getattr(result, "rowcount", None)
                if isinstance(rowcount, int) and rowcount >= 0:
                    op.add_metadata("affected_rows", rowcount)
                return result

        return sync_wrapper  # type: ignore[return-value]

    return decorator


class AMGRInstrumentedCursor:
    """Wrapper around a DB-API 2.0 cursor to record database telemetry."""

    def __init__(
        self,
        cursor: Any,
        destination: ComponentRef,
        recorder: EventRecorder | None = None,
        data_labels: Sequence[str] | str | None = None,
        source: ComponentRef | None = None,
    ) -> None:
        self._cursor = cursor
        self._destination = destination
        self._recorder = recorder
        self._data_labels = data_labels
        self._source = source

    def execute(self, operation: Any, parameters: Any = None) -> Any:
        sql = str(operation) if operation else ""
        category = classify_sql_operation(sql)
        with db_span(
            operation=category,
            destination=self._destination,
            source=self._source,
            data_labels=self._data_labels,
            recorder=self._recorder,
        ) as op:
            if parameters is not None:
                result = self._cursor.execute(operation, parameters)
            else:
                result = self._cursor.execute(operation)
            rowcount = getattr(self._cursor, "rowcount", -1)
            if isinstance(rowcount, int) and rowcount >= 0:
                op.add_metadata("affected_rows", rowcount)
            return result

    def executemany(self, operation: Any, seq_of_parameters: Any) -> Any:
        sql = str(operation) if operation else ""
        category = classify_sql_operation(sql)
        with db_span(
            operation=category,
            destination=self._destination,
            source=self._source,
            data_labels=self._data_labels,
            recorder=self._recorder,
        ) as op:
            result = self._cursor.executemany(operation, seq_of_parameters)
            rowcount = getattr(self._cursor, "rowcount", -1)
            if isinstance(rowcount, int) and rowcount >= 0:
                op.add_metadata("affected_rows", rowcount)
            return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __iter__(self) -> Any:
        return iter(self._cursor)


class AMGRInstrumentedConnection:
    """Wrapper around a DB-API 2.0 connection to produce instrumented cursors."""

    def __init__(
        self,
        connection: Any,
        destination: ComponentRef,
        recorder: EventRecorder | None = None,
        data_labels: Sequence[str] | str | None = None,
        source: ComponentRef | None = None,
    ) -> None:
        self._connection = connection
        self._destination = destination
        self._recorder = recorder
        self._data_labels = data_labels
        self._source = source

    def cursor(self, *args: Any, **kwargs: Any) -> AMGRInstrumentedCursor:
        cur = self._connection.cursor(*args, **kwargs)
        return AMGRInstrumentedCursor(
            cursor=cur,
            destination=self._destination,
            recorder=self._recorder,
            data_labels=self._data_labels,
            source=self._source,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def instrument_dbapi_connection(
    connection: Any,
    *,
    destination: ComponentRef | str | None = None,
    recorder: EventRecorder | None = None,
    client: Any | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
) -> AMGRInstrumentedConnection:
    """Instrument a DB-API 2.0 compliant connection (e.g. sqlite3, psycopg).

    Opt-in: wraps only the provided connection instance without global monkey-patching.
    """
    dest = derive_database_ref(destination)
    rec = _resolve_recorder(client, recorder)
    return AMGRInstrumentedConnection(
        connection=connection,
        destination=dest,
        recorder=rec,
        data_labels=data_labels,
        source=source,
    )


def instrument_sqlalchemy_engine(
    engine: Any,
    *,
    recorder: EventRecorder | None = None,
    client: Any | None = None,
    destination: ComponentRef | str | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
) -> Any:
    """Instrument a SQLAlchemy Engine instance using engine event listeners.

    Raises:
        IntegrationError: If the 'sqlalchemy' package is not installed.
    """
    try:
        from sqlalchemy import event
    except ImportError as err:
        raise IntegrationError(
            "The 'sqlalchemy' package is not installed in the current environment. "
            "Install it via 'pip install sqlalchemy' to use SQLAlchemy engine instrumentation."
        ) from err

    dest = derive_database_ref(
        destination
        or (str(engine.url) if hasattr(engine, "url") else "sqlalchemy-database")
    )
    rec = _resolve_recorder(client, recorder)
    labels = _resolve_data_labels(data_labels)

    def before_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if rec is None or context is None:
            return
        category = classify_sql_operation(statement)
        op = rec.start_operation(
            action="db.query",
            destination=dest,
            source=source,
            data_labels=labels,
            metadata={"operation": category},
            enter_context=True,
        )
        setattr(context, "_amgr_db_op", op)

    def after_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        op: ActiveOperation | None = getattr(context, "_amgr_db_op", None)
        if op is not None:
            rowcount = getattr(cursor, "rowcount", -1)
            if isinstance(rowcount, int) and rowcount >= 0:
                op.add_metadata("affected_rows", rowcount)
            op.succeed(status="completed")

    def handle_error(context: Any) -> None:
        if context is not None:
            op: ActiveOperation | None = getattr(context, "_amgr_db_op", None)
            if op is not None:
                exc = getattr(context, "original_exception", None) or Exception(
                    "Database operation failed"
                )
                op.fail(error=exc, status="failed")

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    event.listen(engine, "handle_error", handle_error)

    return engine
