try:
    from backend.app.db.session import Base
except ImportError:
    from app.db.session import Base

__all__ = ["Base"]