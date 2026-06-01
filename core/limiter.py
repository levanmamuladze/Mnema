from slowapi import Limiter
from slowapi.util import get_remote_address

from core.config import settings

# Single shared limiter instance imported by both main.py and routers.
#
# storage_uri defaults to "memory://" (per-process). Set RATE_LIMIT_STORAGE_URI
# to a Redis URL (e.g. redis://redis:6379/0) once you run more than one worker,
# otherwise each worker keeps its own counts and the effective limit multiplies.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/minute"],
    storage_uri=settings.RATE_LIMIT_STORAGE_URI,
)
