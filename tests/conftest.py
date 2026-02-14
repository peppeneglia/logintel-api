"""
Shared test fixtures.

Automatically injects fresh InMemoryPredictionStore and InMemoryCalibrationStore
before each test, and ensures auth dev-mode bypass by clearing supabase_url.
"""

from __future__ import annotations

import pytest

import app.stores as _stores_mod
from app.config import get_settings
from app.stores.memory import InMemoryCalibrationStore, InMemoryPredictionStore


@pytest.fixture(autouse=True)
def _use_memory_stores():
    """Inject fresh in-memory stores before each test, restore after."""
    original_pred = _stores_mod.prediction_store
    original_cal = _stores_mod.calibration_store

    _stores_mod.prediction_store = InMemoryPredictionStore()
    _stores_mod.calibration_store = InMemoryCalibrationStore()

    yield

    _stores_mod.prediction_store = original_pred
    _stores_mod.calibration_store = original_cal


@pytest.fixture(autouse=True)
def _dev_mode_auth():
    """Ensure auth dev-mode bypass by clearing supabase_url in settings."""
    settings = get_settings()
    original_url = settings.supabase_url
    settings.supabase_url = ""
    yield
    settings.supabase_url = original_url
