from slowapi import Limiter
from slowapi.util import get_remote_address

# Single shared limiter instance imported by both main.py and routers
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
