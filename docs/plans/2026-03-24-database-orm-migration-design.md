# Database ORM Migration Design

**Date:** 2026-03-24
**Status:** Approved
**Priority:** High (Infrastructure)

## 1. Overview

### Goal

Replace raw SQL queries with SQLAlchemy ORM for better maintainability, type safety, and scalability while preserving SQLite compatibility and enabling easy migration to PostgreSQL or other databases.

### Architecture Changes

- Add SQLAlchemy as dependency in `pyproject.toml`
- Create new `server/models/` directory with declarative models for users, messages, conversations, etc.
- Replace `aiosqlite` direct usage with SQLAlchemy async sessions
- Update all API endpoints and client store methods to use ORM queries instead of raw SQL

### Key Components

- Base model class with common fields (id, created_at, updated_at)
- User model (id, username, password_hash, totp_secret, etc.)
- Message model (id, conversation_id, sender_id, envelope_json, delivery_status, ttl, etc.)
- Conversation model (id, participants as JSON array)
- Async session factory configured for SQLite

### Data Flow

1. API routes get SQLAlchemy session from dependency injection
2. Queries use model attributes instead of string SQL
3. Results automatically mapped to Pydantic models for responses
4. Transactions handled via session context managers

### Trade-offs

- ~20% performance overhead for simple queries
- 50%+ reduction in code complexity and injection risks
- Improved type safety and IDE support

---

## 2. Components Detail

### Base Infrastructure

#### `server/models/__init__.py`

- Import all models
- Define async session factory with database URL parsing

#### `server/models/base.py`

- Declarative base with database-agnostic column types
- Common mixins: timestamps, soft deletes

#### `server/core/database.py`

- Auto-detect database dialect from URL (SQLite, PostgreSQL, MySQL)
- Configure appropriate driver:
  - `aiosqlite` for SQLite
  - `asyncpg` for PostgreSQL
- Set dialect-specific options (e.g., JSON handling: native for PostgreSQL, text-based for SQLite)

### Auto-Detection Logic

- Parse `DATABASE_URL` to determine backend (e.g., `sqlite://` -> SQLite, `postgresql://` -> PostgreSQL)
- Load appropriate async driver dynamically
- Similar to how PHP frameworks (like Laravel) auto-configure based on DB connection string

### Core Models (Database-Agnostic)

```python
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    totp_secret = Column(String)  # Encrypted
    created_at = Column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(String, nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    envelope_json = Column(Text, nullable=False)  # JSON string
    delivery_status = Column(Enum(DeliveryStatus), default=DeliveryStatus.SENT)
    ttl = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    sender = relationship("User")
```

### API Integration

- Session factory dynamically configured at startup
- No code changes needed when switching databases - just update `DATABASE_URL`
- Error handling for unsupported databases or connection failures
- Update FastAPI dependency: `async def get_db() -> AsyncSession:`
- Replace raw SQL in routes with ORM queries:
  ```python
  # Instead of: async with db.execute("SELECT * FROM messages WHERE...")
  messages = await db.execute(select(Message).where(Message.conversation_id == conv_id))
  ```

### Benefits

- Framework-like simplicity - "set URL and it works"
- Auto-adapts to database capabilities
- Easy scaling: start with SQLite for development, switch to PostgreSQL for production without code changes

---

## 3. Error Handling & Testing

### Error Handling

- **Connection failures:** Retry logic with exponential backoff, fallback to cached responses if applicable
- **Migration errors:** Alembic provides rollback on failures, log detailed errors for debugging
- **Query errors:** SQLAlchemy exceptions translated to HTTP 500 with sanitized messages (no SQL leakage)
- **Database-specific errors:** Handle dialect differences (e.g., PostgreSQL unique violations vs SQLite constraint errors)
- **Graceful degradation:** If database unavailable, return 503 Service Unavailable

### Testing Strategy

- **Unit tests:** Mock SQLAlchemy sessions, test model relationships and validations
- **Integration tests:** Use test database (SQLite in-memory or PostgreSQL test instance)
- **Migration tests:** Alembic upgrade/downgrade tests to ensure schema consistency
- **Performance tests:** Compare query times before/after migration, ensure <10% regression
- **Database compatibility tests:** Run test suite against SQLite, PostgreSQL, and MySQL

### Data Flow with Error Handling

1. API receives request -> Get session from pool
2. Execute ORM query -> Catch SQLAlchemy exceptions -> Log and return error response
3. Commit transaction -> Handle integrity errors -> Rollback if needed
4. Return results -> Close session

### Migration Rollout

- Feature flag to toggle between raw SQL and ORM
- Gradual rollout: Enable for read operations first, then writes
- Monitoring: Track query performance and error rates during transition

---

## 4. Migration Strategy

### Phase 1: Setup & Models

1. Add SQLAlchemy and Alembic dependencies to `pyproject.toml`
2. Create `server/models/` directory with base and core models
3. Update `server/core/database.py` with auto-detection logic
4. Add `DATABASE_URL` environment variable support

### Phase 2: API Migration

1. Update FastAPI dependency injection for SQLAlchemy sessions
2. Migrate read operations first (GET endpoints)
3. Migrate write operations (POST endpoints)
4. Update client store methods for local SQLite operations

### Phase 3: Testing & Validation

1. Run full test suite against SQLite
2. Run compatibility tests against PostgreSQL
3. Performance benchmarking
4. Security audit for injection risks

### Phase 4: Deployment

1. Feature flag rollout in staging
2. Monitor error rates and performance
3. Gradual production rollout
4. Remove raw SQL code after validation

---

## 5. Files to Modify

### New Files

- `server/models/__init__.py`
- `server/models/base.py`
- `server/models/user.py`
- `server/models/message.py`
- `server/models/conversation.py`
- `server/models/friend_request.py`
- `server/models/delivery_status.py`
- `server/models/rate_limit.py`
- `alembic.ini`
- `alembic/` directory (migrations)

### Modified Files

- `pyproject.toml` (add dependencies)
- `server/core/database.py` (auto-detection logic)
- `server/api/auth.py` (use ORM)
- `server/api/messages.py` (use ORM)
- `server/api/friends.py` (use ORM)
- `server/api/conversations.py` (use ORM)
- `server/api/keys.py` (use ORM)
- `client/state/store.py` (use ORM for local storage)
- `server/main.py` (session factory setup)
- `.env.example` (add DATABASE_URL)

---

## 6. Dependencies

### New Dependencies

- `sqlalchemy[asyncio]>=2.0.0`
- `alembic>=1.13.0`
- `asyncpg>=0.29.0` (PostgreSQL driver)
- `aiosqlite>=0.20.0` (SQLite driver, already present)

### Optional Dependencies

- `psycopg2-binary>=2.9.0` (alternative PostgreSQL driver)

---

## 7. Security Considerations

- SQL injection prevention through parameterized ORM queries
- Sensitive data (passwords, TOTP secrets) remain encrypted at rest
- Database connection strings secured via environment variables
- Audit logging for database operations (future enhancement)
- Rate limiting preserved through ORM queries

---

## 8. Performance Considerations

- Connection pooling configured for production workloads
- Query optimization through SQLAlchemy's query builder
- Indexes defined on frequently queried columns
- Lazy loading for relationships to avoid N+1 queries
- Caching layer for read-heavy operations (future enhancement)
