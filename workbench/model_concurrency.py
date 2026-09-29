"""Bounded model and dossier-batch concurrency.

This does not parallelize dependent case mutations. The controller owns those
dependencies and evidence adoption; providers only return proposals.

``lease`` is the backwards-compatible request guard.  ``batch_lease`` adds the
small scheduling primitive needed by a caller which wants to dispatch
independent dossier batches: a ``(case_id, batch_id)`` can be reserved only
once, and no more than two reservations may be in flight.  Endpoint
parallelism remains one unless the caller explicitly configures that endpoint.
"""
from contextlib import contextmanager
import threading

_total=threading.BoundedSemaphore(2)
_registry_lock=threading.Lock()
_connections={}
_connection_limits={}
_batch_active=set()
_case_active={}
_batch_total=0


def configure_endpoint(identity, *, limit=1):
    """Explicitly configure parallel requests for one endpoint identity.

    The default is intentionally serial per endpoint (including local Ollama).
    A test-only transport may opt into two requests by calling this function;
    no model or provider name is inspected here.  Configuration must happen
    before requests begin, otherwise changing a live semaphore could strand
    permits or violate the endpoint's safety bound.
    """
    if not isinstance(identity, str) or not identity:
        raise ValueError('endpoint identity is required')
    if not isinstance(limit, int) or limit < 1 or limit > 2:
        raise ValueError('endpoint concurrency must be 1 or 2')
    with _registry_lock:
        if identity in _connections:
            current=_connection_limits.get(identity,1)
            if current!=limit:
                raise RuntimeError('endpoint concurrency is already in use with a different limit')
            return
        if identity in _connection_limits:
            if _connection_limits[identity]!=limit:
                raise RuntimeError('endpoint concurrency is already configured with a different limit')
            return
        _connection_limits[identity]=limit


def endpoint_limit(identity):
    """Return the configured endpoint limit, without creating a connection."""
    with _registry_lock:
        return _connection_limits.get(identity, 1)


class _BatchToken:
    def __init__(self, case_id, batch_id):
        self.case_id=case_id
        self.batch_id=batch_id
        self._released=False

    def release(self):
        global _batch_total
        with _registry_lock:
            if self._released:
                return
            self._released=True
            _batch_active.discard((self.case_id,self.batch_id))
            count=_case_active.get(self.case_id,0)-1
            if count:
                _case_active[self.case_id]=count
            else:
                _case_active.pop(self.case_id,None)
            _batch_total-=1


def try_reserve_batch(case_id, batch_id):
    """Atomically reserve an independent dossier batch, or return ``None``.

    Reservation is deliberately separate from database mutation: the caller
    must still atomically mark the batch's attempt in its store transaction and
    release this token on every exit path.  This token only prevents duplicate
    workers and enforces the in-process per-case/global bounds.
    """
    global _batch_total
    key=(case_id,batch_id)
    with _registry_lock:
        if key in _batch_active or _batch_total >= 2 or _case_active.get(case_id,0) >= 2:
            return None
        _batch_active.add(key)
        _case_active[case_id]=_case_active.get(case_id,0)+1
        _batch_total+=1
        return _BatchToken(case_id,batch_id)


@contextmanager
def batch_lease(case_id, batch_id, identity):
    """Reserve a batch and its model endpoint without blocking the scheduler.

    The context yields ``False`` when another worker already owns the batch or
    when the bounded scheduler is full; callers should leave the batch queued.
    """
    token=try_reserve_batch(case_id,batch_id)
    if token is None:
        yield False
        return
    try:
        with lease(identity):
            yield True
    finally:
        token.release()


@contextmanager
def lease(identity):
    with _registry_lock:
        connection=_connections.get(identity)
        if connection is None:
            # Configure before first use to opt into endpoint parallelism.
            connection=threading.BoundedSemaphore(_connection_limits.get(identity,1))
            _connections[identity]=connection
    # Waiting for the same model must not monopolize both global slots.
    with connection:
        with _total:
            yield
