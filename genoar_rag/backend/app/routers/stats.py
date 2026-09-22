"""Dashboard statistics endpoint."""

import sqlite3

from fastapi import APIRouter, Depends

from app.dependencies import get_db, get_dataset
from app.models.stats import DashboardStats
from app.services.stats_service import StatsService

router = APIRouter(prefix="/api/v1", tags=["stats"])


@router.get("/stats", response_model=DashboardStats)
def get_stats(
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
):
    svc = StatsService(conn, dataset)
    return svc.get_dashboard_stats()
