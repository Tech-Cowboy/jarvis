"""Business systems: what Daxton can read from the company's system of record.

Read-only by construction (see `daxton/business/odoo.py`): the assistant can look up bookings, customers,
leads, sales, the inbox and reminders, and can never change a record, send a message or move money.
"""

from __future__ import annotations

from .odoo import OdooError, OdooNotConfigured, OdooReader, credentials_from

__all__ = ["OdooError", "OdooNotConfigured", "OdooReader", "credentials_from"]
