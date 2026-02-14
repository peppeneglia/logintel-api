"""
Store factory — selects in-memory or Supabase implementations at startup.

Import ``prediction_store`` and ``calibration_store`` from this module.
Call ``init_stores()`` during app lifespan to swap to Supabase when configured.
"""

from __future__ import annotations

import logging

from app.stores.memory import InMemoryCalibrationStore, InMemoryPredictionStore

logger = logging.getLogger(__name__)

# Module-level singletons — start with in-memory
prediction_store: InMemoryPredictionStore = InMemoryPredictionStore()
calibration_store: InMemoryCalibrationStore = InMemoryCalibrationStore()


def init_stores() -> None:
    """Swap singletons to Supabase implementations if configured."""
    global prediction_store, calibration_store

    from app.services.supabase import is_configured

    if is_configured():
        from app.stores.supabase_store import (
            SupabaseCalibrationStore,
            SupabasePredictionStore,
        )

        prediction_store = SupabasePredictionStore()  # type: ignore[assignment]
        calibration_store = SupabaseCalibrationStore()  # type: ignore[assignment]
        logger.info("Stores: using Supabase persistence")
    else:
        logger.info("Stores: using in-memory (data lost on restart)")
