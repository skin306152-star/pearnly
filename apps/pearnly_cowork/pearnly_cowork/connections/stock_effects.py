"""Apply a verified legacy ACK delta to its account set's Account Facts.

Does not invent directory evidence or write ERP stock. The push ledger owns
idempotency and transaction rollback. A new machine scan remains authoritative.
Account Facts are per (connection, canonical account set key), whichever computer
read them (ADR-021); the registration's canonical root must still be the frozen one.
"""
from . import pool, state


def apply(connection, target, count):
    if not count:
        return {'count': 0, 'state': 'not_applicable'}
    doc = state.read(state.CONNECTION, connection)
    if doc.adapter != 'express' or doc.binding_generation:
        return {'count': count, 'state': 'target_changed'}
    return pool.add_stock_masters(doc.name, target.get('account_set'), count, root=target.get('root_key'))
