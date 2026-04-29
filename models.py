from sqlalchemy import (
    Column, BigInteger, String, Boolean,
    DateTime, Text, ForeignKey, Integer, CHAR
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id         = Column(BigInteger, primary_key=True)
    name       = Column(String(50),  nullable=False)
    created_at = Column(DateTime,    server_default=func.now())

    sessions   = relationship("Session", back_populates="user")


class Session(Base):
    __tablename__ = "session"

    id           = Column(BigInteger, primary_key=True)
    user_id      = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    raw_filename = Column(String(255))
    created_at   = Column(DateTime, server_default=func.now())

    user   = relationship("User",   back_populates="sessions")
    nouns  = relationship("Noun",   back_populates="session")
    albums = relationship("Album",  back_populates="session")
    qa_logs= relationship("QaLog",  back_populates="session")


class QaLog(Base):
    __tablename__ = "qa_log"

    id          = Column(BigInteger, primary_key=True)
    session_id  = Column(BigInteger, ForeignKey("session.id"), nullable=False)
    type        = Column(CHAR(1),    nullable=False)   # 'Q' or 'A'
    text        = Column(Text,       nullable=False)
    order_index = Column(Integer,    nullable=False)

    session = relationship("Session", back_populates="qa_logs")


class Noun(Base):
    __tablename__ = "noun"

    id           = Column(BigInteger, primary_key=True)
    session_id   = Column(BigInteger, ForeignKey("session.id"), nullable=False)
    value        = Column(String(100), nullable=False)
    target_noun  = Column(String(100))
    is_base_noun = Column(Boolean, default=False)

    session    = relationship("Session",   back_populates="nouns")
    adjectives = relationship("Adjective", back_populates="noun")
    verbs      = relationship("Verb",      back_populates="noun")


class Adjective(Base):
    __tablename__ = "adjective"

    id      = Column(BigInteger, primary_key=True)
    noun_id = Column(BigInteger, ForeignKey("noun.id"), nullable=False)
    value   = Column(String(100), nullable=False)

    noun = relationship("Noun", back_populates="adjectives")


class Verb(Base):
    __tablename__ = "verb"

    id      = Column(BigInteger, primary_key=True)
    noun_id = Column(BigInteger, ForeignKey("noun.id"), nullable=False)
    value   = Column(String(100), nullable=False)

    noun = relationship("Noun", back_populates="verbs")


class Album(Base):
    __tablename__ = "album"

    id         = Column(BigInteger, primary_key=True)
    session_id = Column(BigInteger, ForeignKey("session.id"), nullable=False)
    image_url  = Column(Text,       nullable=False)
    prompt     = Column(Text)
    created_at = Column(DateTime,   server_default=func.now())

    session = relationship("Session", back_populates="albums")
