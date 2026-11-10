from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    api_token = Column(String(64), unique=True, nullable=True)
    agent_seen_at = Column(DateTime, nullable=True)  # último pedido del agente (analítica)

    settings = relationship("UserSettings", back_populates="user", uselist=False)

    @property
    def agent_online(self) -> bool:
        """Mismo criterio que la analítica (health.AGENT_ONLINE); lo usa el indicador de la nav."""
        from datetime import timedelta
        if not self.agent_seen_at:
            return False
        seen = self.agent_seen_at if self.agent_seen_at.tzinfo else self.agent_seen_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - seen <= timedelta(minutes=2)

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} admin={self.is_admin}>"
