"""
Store factory — selects in-memory or Supabase implementations at startup.

Access the active stores as ``app.stores.prediction_store`` and
``app.stores.calibration_store`` (module attributes, not ``from`` imports,
so that ``init_stores()`` and test fixtures can swap them).
"""

from __future__ import annotations

import logging

from app.stores.base import CalibrationStore, PredictionStore
from app.stores.memory import InMemoryCalibrationStore, InMemoryPredictionStore

logger = logging.getLogger(__name__)

# Module-level singletons — start with in-memory
prediction_store: PredictionStore = InMemoryPredictionStore()
calibration_store: CalibrationStore = InMemoryCalibrationStore()


def init_stores() -> None:
    """Swap singletons to Supabase implementations if configured."""
    global prediction_store, calibration_store

    from app.services.supabase import is_configured

    if is_configured():
        from app.stores.supabase_store import SupabaseCalibrationStore, SupabasePredictionStore

        prediction_store = SupabasePredictionStore()
        calibration_store = SupabaseCalibrationStore()
        logger.info("Stores: using Supabase persistence")
    else:
        logger.info("Stores: using in-memory (data lost on restart)")
