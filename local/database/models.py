import enum
from datetime import datetime

from sqlalchemy import TIMESTAMP, BigInteger, Boolean, Column
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class UserRole(enum.Enum):
    """Роли пользователей"""

    JUNIOR = "junior"
    SENIOR = "senior"
    ADMIN = "admin"


class DialogStatus(enum.Enum):
    """Статусы диалога"""

    ACTIVE = "active"
    RESOLVED = "resolved"


class SenderType(enum.Enum):
    """Тип отправителя сообщения"""

    USER = "user"
    BOT = "bot"


class User(Base):
    """Модель пользователя"""

    __tablename__ = "users"

    user_id = Column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(100), nullable=True)
    full_name = Column(String(255), nullable=True)
    role = Column(SQLEnum(UserRole), nullable=False, default=UserRole.JUNIOR)
    is_active = Column(Boolean, default=True, nullable=False)
    is_blocked = Column(Boolean, default=False, nullable=False)
    created_at = Column(TIMESTAMP, default=func.current_timestamp(), nullable=False)
    last_activity = Column(TIMESTAMP, nullable=True)
    consultation_count = Column(Integer, default=0, nullable=False)

    # Связи
    dialogs = relationship(
        "Dialog", back_populates="user", cascade="all, delete-orphan"
    )
    messages = relationship(
        "Message", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User(telegram_id={self.telegram_id}, username={self.username}, role={self.role.value})>"


class Dialog(Base):
    """Модель диалога"""

    __tablename__ = "dialogs"

    dialog_id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    started_at = Column(TIMESTAMP, default=func.current_timestamp(), nullable=False)
    ended_at = Column(TIMESTAMP, nullable=True)
    status = Column(SQLEnum(DialogStatus), default=DialogStatus.ACTIVE, nullable=False)
    llm_model_used = Column(String(50), nullable=True)
    total_tokens_used = Column(Integer, default=0, nullable=False)

    # Связи
    user = relationship("User", back_populates="dialogs")
    messages = relationship(
        "Message",
        back_populates="dialog",
        cascade="all, delete-orphan",
        order_by="Message.sent_at",
    )

    def __repr__(self):
        return f"<Dialog(dialog_id={self.dialog_id}, user_id={self.user_id}, status={self.status.value})>"


class Message(Base):
    """Модель сообщения"""

    __tablename__ = "messages"

    message_id = Column(BigInteger, primary_key=True, autoincrement=True)
    dialog_id = Column(
        BigInteger,
        ForeignKey("dialogs.dialog_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    context = Column(Text, nullable=False)
    sender_type = Column(SQLEnum(SenderType), nullable=False)
    sent_at = Column(TIMESTAMP, default=func.current_timestamp(), nullable=False)
    telegram_message_id = Column(BigInteger, nullable=True)
    tokens_used = Column(Integer, default=0, nullable=False)

    # Связи
    dialog = relationship("Dialog", back_populates="messages")
    user = relationship("User", back_populates="messages")

    def __repr__(self):
        return f"<Message(message_id={self.message_id}, dialog_id={self.dialog_id}, sender={self.sender_type.value})>"
