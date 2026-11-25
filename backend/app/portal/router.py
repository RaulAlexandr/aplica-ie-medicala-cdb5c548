"""T14 patient portal: one router for ``main.py`` with the patient and the staff routes."""
from fastapi import APIRouter

from app.portal.patient_router import router as patient_router
from app.portal.staff_router import router as staff_router

router = APIRouter()
router.include_router(patient_router)
router.include_router(staff_router)
