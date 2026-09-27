"""Authentication API endpoints"""

import asyncio
import hashlib
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ...config import get_app_base_url, get_password_reset_expire_minutes
from ...core.agent.voice_policy import VALID_VOICES as _CORE_VALID_VOICES
from ...core.runtime_performance import (
    increment_counter as increment_performance_counter,
)
from ...core.runtime_performance import (
    observe_duration,
    observe_value,
)
from ..auth_config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    PASSWORD_MIN_LENGTH,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from ..auth_dependencies import get_current_user
from ..first_admin_setup import FirstAdminIdentity, run_first_admin_setup_hook
from ..models.auth_database import (
    SyncAuthSessionFactory,
    get_auth_db,
    run_auth_db_worker,
)
from ..models.database import (
    get_db,
    get_session_local,
    release_db_connection_if_clean,
)
from ..models.system_setting import SystemSetting
from ..models.user import User
from ..services.auth_email import send_password_reset_email
from ..services.db_runtime import await_task_settlement, propagate_deferred_cancellation

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/auth", tags=["Authentication"])

REGISTRATION_ENABLED_SETTING_KEY = "registration_enabled"
SETUP_COMPLETED_SETTING_KEY = "setup_completed"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_USER_ID = 2_147_483_647


def create_access_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode.update({"exp": expire})
    if "type" not in to_encode:
        to_encode["type"] = "access"
    encoded_jwt: str = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: Dict[str, Any]) -> str:
    """Create JWT refresh token with longer expiry"""
    # Refresh consumes user_id, never username/sub. Omit that redundant,
    # unbounded UTF-8 claim to keep the signed token within VARCHAR(255).
    # Access tokens retain sub; verification still accepts existing refresh JWTs.
    to_encode = {"user_id": data["user_id"]}
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    # Rotation must change the stored value even within one JWT timestamp second.
    to_encode.update(
        {"exp": expire, "type": "refresh", "jti": secrets.token_urlsafe(16)}
    )
    encoded_jwt: str = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def verify_refresh_token(token: str) -> Optional[dict[str, Any]]:
    """Verify JWT refresh token and return payload"""
    try:
        payload: dict[str, Any] = jwt.decode(
            token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None


def verify_token(token: str) -> Optional[dict[str, Any]]:
    """Verify JWT token and return payload"""
    try:
        payload: dict[str, Any] = jwt.decode(
            token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        return None


class LoginRequest(BaseModel):
    """Login request model"""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Login response model"""

    success: bool
    message: str
    user: Optional[Dict[str, Any]] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    user_id: Optional[int] = None
    expires_in: Optional[int] = None
    refresh_expires_in: Optional[int] = None


class RegisterRequest(BaseModel):
    """User registration request model"""

    username: str
    email: Optional[str] = None
    password: str


class RegisterResponse(BaseModel):
    """User registration response model"""

    success: bool
    message: str
    user: Optional[Dict[str, Any]] = None


class SetupStatusResponse(BaseModel):
    initialized: bool
    needs_setup: bool
    registration_enabled: bool


class RegisterSwitchRequest(BaseModel):
    enabled: bool


class RegisterSwitchResponse(BaseModel):
    success: bool
    registration_enabled: bool
    message: str


class ChangePasswordRequest(BaseModel):
    """Change password request model"""

    current_password: str
    new_password: str


class ChangePasswordResponse(BaseModel):
    """Change password response model"""

    success: bool
    message: str


class UserProfileResponse(BaseModel):
    success: bool
    message: str
    user: Dict[str, Any]


class UpdateEmailRequest(BaseModel):
    email: str


class UpdateEmailResponse(BaseModel):
    success: bool
    message: str
    user: Optional[Dict[str, Any]] = None


# The 5 voice options the onboarding "Launch" step offers - each agent's
# system prompt gets a short instruction derived from whichever one the
# user picked (see apply_user_voice in api/agents.py). Re-exported from
# core.agent.voice_policy (the canonical source, not redeclared here) so
# this and _VOICE_INSTRUCTIONS there can never drift into two
# independently-maintained copies of the same 5-string set.
VALID_USER_VOICES = _CORE_VALID_VOICES

# Onboarding's "About you"/"Goals" steps are short free-text labels and a
# handful of goal picks, not open-ended documents - these mirror the
# max_length this codebase already puts on comparably-scoped free-text
# fields (e.g. agents.py's/custom_api.py's name/description Fields).
# Without a bound, the full preferences JSON is replayed through every
# login/`/me`/email-update/token-validation/PATCH response, so an
# unbounded field lets a user inflate all of those response bodies.
PREFERENCES_TEXT_FIELD_MAX_LENGTH = 200
PREFERENCES_GOALS_MAX_ITEMS = 20


class UpdatePreferencesRequest(BaseModel):
    """Partial update for the current user's onboarding/voice preferences.
    Only fields actually present in the request body are merged into the
    stored dict (see exclude_unset=True below) - onboarding writes these
    incrementally, one step at a time, not all at once."""

    # Pydantic's default (extra="ignore") would validate a typo'd key
    # (e.g. "voce") to an empty, all-unset model: model_dump(exclude_unset=True)
    # then returns {}, so the PATCH silently skips persistence and cache
    # invalidation while still reporting success - forbid so a typo/unknown
    # key is a 422, not a lost write the client believes succeeded.
    model_config = ConfigDict(extra="forbid")

    onboarded: Optional[StrictBool] = None
    department: Optional[str] = Field(
        default=None, max_length=PREFERENCES_TEXT_FIELD_MAX_LENGTH
    )
    industry: Optional[str] = Field(
        default=None, max_length=PREFERENCES_TEXT_FIELD_MAX_LENGTH
    )
    voice: Optional[str] = None
    goals: Optional[
        List[Annotated[str, Field(max_length=PREFERENCES_TEXT_FIELD_MAX_LENGTH)]]
    ] = Field(default=None, max_length=PREFERENCES_GOALS_MAX_ITEMS)

    @field_validator("voice")
    @classmethod
    def _validate_voice(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in VALID_USER_VOICES:
            raise ValueError(f"voice must be one of {sorted(VALID_USER_VOICES)}")
        return value

    # A blank string is meaningless stored data, not a "clear this field"
    # signal - a merge-style PATCH already has one for that (send `null`
    # for the key, same as tokens_must_not_be_blank rejects a blank
    # access/refresh token above rather than treating it as "no token").
    @field_validator("department", "industry")
    @classmethod
    def _reject_blank_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("goals")
    @classmethod
    def _reject_blank_goals(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return value
        stripped_goals = [item.strip() for item in value]
        if any(not item for item in stripped_goals):
            raise ValueError("goal must not be blank")
        return stripped_goals


class UpdatePreferencesResponse(BaseModel):
    success: bool
    message: str
    user: Optional[Dict[str, Any]] = None


class RefreshTokenRequest(BaseModel):
    """Refresh token request model"""

    refresh_token: str


class RefreshTokenResponse(BaseModel):
    """Refresh token response model"""

    success: Literal[True]
    message: str
    access_token: Annotated[StrictStr, Field(min_length=1)]
    refresh_token: Annotated[StrictStr, Field(min_length=1)]
    expires_in: Annotated[StrictInt, Field(gt=0)]
    refresh_expires_in: Annotated[StrictInt, Field(gt=0)]

    @field_validator("access_token", "refresh_token")
    @classmethod
    def tokens_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Token must not be blank")
        return value


class ForgotPasswordRequest(BaseModel):
    email: str


class ForgotPasswordResponse(BaseModel):
    success: bool
    message: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class ResetPasswordResponse(BaseModel):
    success: bool
    message: str


def hash_password(password: str) -> str:
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash"""
    return hash_password(password) == password_hash


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email))


def hash_password_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_password_reset_token() -> str:
    return secrets.token_urlsafe(32)


def get_app_name() -> str:
    return os.getenv("XAGENT_APP_NAME") or os.getenv("NEXT_PUBLIC_APP_NAME") or "Xagent"


def build_password_reset_url(token: str) -> str:
    base_url = get_app_base_url()
    if not base_url:
        raise RuntimeError(
            "XAGENT_APP_BASE_URL must be configured for password reset emails"
        )
    return f"{base_url}/reset-password?token={token}"


def has_users(db: Session) -> bool:
    return db.query(User.id).first() is not None


def is_registration_enabled(db: Session) -> bool:
    setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == REGISTRATION_ENABLED_SETTING_KEY)
        .first()
    )
    if setting is None:
        return True
    return str(setting.value).lower() == "true"


def set_registration_enabled(db: Session, enabled: bool) -> None:
    setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == REGISTRATION_ENABLED_SETTING_KEY)
        .first()
    )
    value = "true" if enabled else "false"
    if setting is None:
        setting = SystemSetting(key=REGISTRATION_ENABLED_SETTING_KEY, value=value)
        db.add(setting)
    else:
        setattr(setting, "value", value)
    db.commit()


def is_setup_completed(db: Session) -> bool:
    setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == SETUP_COMPLETED_SETTING_KEY)
        .first()
    )
    return setting is not None and str(setting.value).lower() == "true"


@auth_router.get("/setup-status", response_model=SetupStatusResponse)
async def setup_status(db: Session = Depends(get_db)) -> SetupStatusResponse:
    initialized = has_users(db)
    registration_enabled = is_registration_enabled(db)
    return SetupStatusResponse(
        initialized=initialized,
        needs_setup=not initialized,
        registration_enabled=registration_enabled,
    )


@auth_router.post("/setup-admin", response_model=RegisterResponse)
async def setup_admin(
    request: RegisterRequest,
    http_request: Request,
    db: Session = Depends(get_db),
) -> RegisterResponse:
    if len(request.password) < PASSWORD_MIN_LENGTH:
        return RegisterResponse(
            success=False,
            message=f"Password must be at least {PASSWORD_MIN_LENGTH} characters",
        )

    try:
        if has_users(db) or is_setup_completed(db):
            return RegisterResponse(success=False, message="Setup already completed")

        existing_user = get_user_by_username(db, request.username)
        if existing_user:
            return RegisterResponse(success=False, message="Username already exists")

        username_namespace_error = validate_username_for_login_namespace(
            db, request.username
        )
        if username_namespace_error:
            return RegisterResponse(success=False, message=username_namespace_error)

        email = None
        if request.email:
            email = normalize_email(request.email)
            if not is_valid_email(email):
                return RegisterResponse(success=False, message="Invalid email address")
            email_namespace_error = validate_email_for_login_namespace(db, email)
            if email_namespace_error:
                return RegisterResponse(success=False, message=email_namespace_error)
            existing_email_user = get_user_by_email(db, email)
            if existing_email_user:
                return RegisterResponse(success=False, message="Email already exists")

        user = User(
            username=request.username,
            email=email,
            password_hash=hash_password(request.password),
            is_admin=True,
        )
        db.add(user)
        db.flush()

        setup_setting = SystemSetting(key=SETUP_COMPLETED_SETTING_KEY, value="true")
        db.add(setup_setting)

        run_first_admin_setup_hook(
            http_request.app, db, FirstAdminIdentity(user_id=int(user.id))
        )
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        return RegisterResponse(success=False, message="Setup already completed")
    except Exception:
        db.rollback()
        raise

    return RegisterResponse(
        success=True,
        message="Administrator account created successfully",
        user={
            "id": user.id,
            "username": user.username,
            "is_admin": bool(cast(Any, user.is_admin)),
            "createdAt": (
                cast(Any, user.created_at).isoformat()
                if getattr(user, "created_at", None) is not None
                else None
            ),
        },
    )


@auth_router.get("/register-switch", response_model=RegisterSwitchResponse)
async def get_register_switch(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> RegisterSwitchResponse:
    if not bool(cast(Any, user.is_admin)):
        raise HTTPException(status_code=403, detail="Admin privileges required")

    enabled = is_registration_enabled(db)
    return RegisterSwitchResponse(
        success=True,
        registration_enabled=enabled,
        message="Registration switch fetched successfully",
    )


@auth_router.patch("/register-switch", response_model=RegisterSwitchResponse)
async def update_register_switch(
    request: RegisterSwitchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RegisterSwitchResponse:
    if not bool(cast(Any, user.is_admin)):
        raise HTTPException(status_code=403, detail="Admin privileges required")

    set_registration_enabled(db, request.enabled)
    return RegisterSwitchResponse(
        success=True,
        registration_enabled=request.enabled,
        message="Registration switch updated successfully",
    )


def create_user(db: Session, username: str, email: str, password: str) -> User:
    """Create a new user without default model configurations.

    Users will use admin defaults via dynamic fallback logic until they set their own.
    No pre-creation of UserModel or UserDefaultModel records.
    """
    password_hash = hash_password(password)
    user = User(username=username, email=email, password_hash=password_hash)
    db.add(user)
    db.flush()  # Get the user ID without committing
    db.refresh(user)

    # Commit everything together
    db.commit()
    return user


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """Get user by username"""
    return db.query(User).filter(User.username == username).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    normalized_email = normalize_email(email)
    return db.query(User).filter(User.email == normalized_email).first()


def validate_username_for_login_namespace(
    db: Session, username: str, *, current_user_id: int | None = None
) -> Optional[str]:
    normalized_username = username.strip()
    if is_valid_email(normalized_username):
        return "Username cannot be an email address"

    conflicting_email_user = get_user_by_email(db, normalized_username)
    if (
        conflicting_email_user is not None
        and conflicting_email_user.id != current_user_id
    ):
        return "Username conflicts with an existing email"

    return None


def validate_email_for_login_namespace(
    db: Session, email: str, *, current_user_id: int | None = None
) -> Optional[str]:
    normalized_email = normalize_email(email)
    conflicting_username_user = get_user_by_username(db, normalized_email)
    if (
        conflicting_username_user is not None
        and conflicting_username_user.id != current_user_id
    ):
        return "Email conflicts with an existing username"
    return None


def _normalized_preferences(user: User) -> Dict[str, Any]:
    """The user's stored preferences as a plain dict, tolerating a NULL or
    malformed value. ``preferences`` has no nested-type constraint (same
    reasoning as apply_output_voice's isinstance guard on the ``voice``
    value it holds), so a corrupted/hand-edited row could store a non-dict
    JSON value here - ``dict(value or {})`` would raise on any of those
    instead of degrading to an empty dict."""
    preferences = cast(Any, user.preferences)
    return dict(preferences) if isinstance(preferences, dict) else {}


def serialize_auth_user(user: User, include_login_time: bool = False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_admin": bool(cast(Any, user.is_admin)),
        "preferences": _normalized_preferences(user),
    }
    if include_login_time:
        payload["loginTime"] = datetime.now(timezone.utc).timestamp()
    return payload


def get_user_by_login_identifier(db: Session, identifier: str) -> Optional[User]:
    """Resolve an identifier as email first, then fall back to username."""
    login_identifier = identifier.strip()
    if is_valid_email(login_identifier):
        user = get_user_by_email(db, login_identifier)
        if user is not None:
            return user
    return get_user_by_username(db, login_identifier)


def _prepare_login_response(user: User | None, request: LoginRequest) -> Dict[str, Any]:
    """Build detached response data before commit can expire ORM attributes."""
    if user is None:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    with observe_duration("xagent.auth.login.password_verify.duration"):
        valid = verify_password(request.password, str(user.password_hash))
    if not valid:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    with observe_duration("xagent.auth.login.token_creation.duration"):
        identity = {"sub": user.username, "user_id": user.id}
        access_token = create_access_token(
            data=identity,
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        )
        token = create_refresh_token(data=identity)
    setattr(user, "refresh_token", token)
    setattr(
        user,
        "refresh_token_expires_at",
        datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )
    return {
        "success": True,
        "message": "Login successful",
        "user": serialize_auth_user(user, include_login_time=True),
        "access_token": access_token,
        "refresh_token": token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "refresh_expires_in": REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        "user_id": user.id,
    }


def _login_in_worker(
    request: LoginRequest, session_factory: SyncAuthSessionFactory
) -> Dict[str, Any]:
    # The same thread creates, queries, commits and closes the Session.
    with session_factory() as session:
        with observe_duration("xagent.auth.login.sync_lookup.duration"):
            user = get_user_by_login_identifier(session, request.username)
        response = _prepare_login_response(user, request)
        with observe_duration("xagent.auth.login.sync_commit.duration"):
            session.commit()
        # Do not access user here: expire_on_commit=True would issue another SELECT.
        return response


@auth_router.post("/login")
async def login(
    request: LoginRequest,
    db: SyncAuthSessionFactory = Depends(get_auth_db),
) -> Dict[str, Any]:
    """Authenticate with a single worker-owned database transaction."""
    started_at = time.perf_counter()
    outcome = "error"
    try:
        response = await run_auth_db_worker(
            "login_database_transaction", lambda: _login_in_worker(request, db)
        )
        outcome = "succeeded"
        return response
    except HTTPException:
        outcome = "rejected"
        raise
    except Exception:
        # SQL errors may contain bound refresh tokens. Never expose them to clients.
        logger.exception("Login failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during login.",
        )
    finally:
        observe_value(
            "xagent.auth.login.total.duration",
            (time.perf_counter() - started_at) * 1_000.0,
            unit="ms",
            attributes={"outcome": outcome},
        )
        increment_performance_counter(
            "xagent.auth.login.requests",
            attributes={"outcome": outcome},
        )


@auth_router.post("/register", response_model=RegisterResponse)
async def register(
    request: RegisterRequest, db: Session = Depends(get_db)
) -> RegisterResponse:
    """User registration endpoint with default configuration inheritance"""
    try:
        # Validate password length
        if len(request.password) < PASSWORD_MIN_LENGTH:
            return RegisterResponse(
                success=False,
                message=f"Password must be at least {PASSWORD_MIN_LENGTH} characters",
            )

        # Check if user already exists
        existing_user = get_user_by_username(db, request.username)
        if existing_user:
            return RegisterResponse(success=False, message="Username already exists")

        username_namespace_error = validate_username_for_login_namespace(
            db, request.username
        )
        if username_namespace_error:
            return RegisterResponse(success=False, message=username_namespace_error)

        initialized = has_users(db)
        if not initialized:
            return RegisterResponse(
                success=False,
                message="System is not initialized. Please create the first admin account.",
            )

        if not is_registration_enabled(db):
            return RegisterResponse(success=False, message="Registration is disabled")

        if not request.email:
            return RegisterResponse(success=False, message="Email is required")

        email = normalize_email(request.email)
        if not is_valid_email(email):
            return RegisterResponse(success=False, message="Invalid email address")

        email_namespace_error = validate_email_for_login_namespace(db, email)
        if email_namespace_error:
            return RegisterResponse(success=False, message=email_namespace_error)

        existing_email_user = get_user_by_email(db, email)
        if existing_email_user:
            return RegisterResponse(success=False, message="Email already exists")

        # Create new user with inherited defaults
        user = create_user(db, request.username, email, request.password)

        return RegisterResponse(
            success=True,
            message="Registration successful",
            user={
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "createdAt": (
                    cast(Any, user.created_at).isoformat()
                    if getattr(user, "created_at", None) is not None
                    else None
                ),
            },
        )

    except Exception:
        # Same reasoning as login's handler: create_user's db.commit() binds
        # password_hash as a SQL parameter, which a SQLAlchemy
        # StatementError's default __str__ would otherwise put into str(e)
        # and, via this response, into the client-facing error.
        logger.exception("Registration failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during registration.",
        )


@auth_router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> ForgotPasswordResponse:
    email = normalize_email(request.email)
    if not is_valid_email(email):
        return ForgotPasswordResponse(
            success=False,
            message="Please enter a valid email address",
        )

    user = get_user_by_email(db, email)
    if user is None:
        return ForgotPasswordResponse(
            success=True,
            message="If the email exists, a password reset link has been sent",
        )

    reset_token = generate_password_reset_token()
    reset_token_hash = hash_password_reset_token(reset_token)
    reset_expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=get_password_reset_expire_minutes()
    )

    setattr(user, "password_reset_token_hash", reset_token_hash)
    setattr(user, "password_reset_expires_at", reset_expires_at)
    db.commit()

    try:
        reset_link = build_password_reset_url(reset_token)
        await asyncio.to_thread(
            send_password_reset_email,
            email,
            reset_link,
            get_app_name(),
        )
    except Exception as exc:
        setattr(user, "password_reset_token_hash", None)
        setattr(user, "password_reset_expires_at", None)
        db.commit()
        logger.error("Failed to send password reset email to %s: %s", email, exc)

    return ForgotPasswordResponse(
        success=True,
        message="If the email exists, a password reset link has been sent",
    )


@auth_router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> ResetPasswordResponse:
    if len(request.new_password) < PASSWORD_MIN_LENGTH:
        return ResetPasswordResponse(
            success=False,
            message=f"New password must be at least {PASSWORD_MIN_LENGTH} characters",
        )

    token_hash = hash_password_reset_token(request.token)
    user = db.query(User).filter(User.password_reset_token_hash == token_hash).first()
    if user is None:
        return ResetPasswordResponse(success=False, message="Invalid reset token")

    reset_expires_at = getattr(user, "password_reset_expires_at", None)
    now = datetime.now(timezone.utc)
    if reset_expires_at is None:
        return ResetPasswordResponse(success=False, message="Invalid reset token")
    if (
        hasattr(reset_expires_at, "tzinfo")
        and getattr(reset_expires_at, "tzinfo", None) is not None
    ):
        if cast(Any, reset_expires_at) < now:
            return ResetPasswordResponse(
                success=False, message="Reset token has expired"
            )
    else:
        if cast(Any, reset_expires_at) < now.replace(tzinfo=None):
            return ResetPasswordResponse(
                success=False, message="Reset token has expired"
            )

    setattr(user, "password_hash", hash_password(request.new_password))
    setattr(user, "password_reset_token_hash", None)
    setattr(user, "password_reset_expires_at", None)
    setattr(user, "refresh_token", None)
    setattr(user, "refresh_token_expires_at", None)
    db.commit()

    return ResetPasswordResponse(
        success=True,
        message="Password has been reset successfully",
    )


@auth_router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChangePasswordResponse:
    """Change user password endpoint"""
    try:
        # Verify current password
        if not verify_password(request.current_password, str(user.password_hash)):
            return ChangePasswordResponse(
                success=False, message="Current password is incorrect"
            )

        # Validate new password
        if len(request.new_password) < PASSWORD_MIN_LENGTH:
            return ChangePasswordResponse(
                success=False,
                message=f"New password must be at least {PASSWORD_MIN_LENGTH} characters",
            )

        # Update password
        setattr(user, "password_hash", hash_password(request.new_password))
        db.commit()

        return ChangePasswordResponse(
            success=True, message="Password updated successfully"
        )

    except Exception:
        # Same reasoning as login's handler: the db.commit() above binds the
        # new password_hash as a SQL parameter, which a SQLAlchemy
        # StatementError's default __str__ would otherwise put into str(e)
        # and, via this response, into the client-facing error.
        logger.exception("Password update failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the password.",
        )


@auth_router.get("/me", response_model=UserProfileResponse)
async def get_current_user_profile(
    user: User = Depends(get_current_user),
) -> UserProfileResponse:
    return UserProfileResponse(
        success=True,
        message="User profile fetched successfully",
        user=serialize_auth_user(user),
    )


@auth_router.patch("/email", response_model=UpdateEmailResponse)
async def update_current_user_email(
    request: UpdateEmailRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UpdateEmailResponse:
    email = normalize_email(request.email)
    if not is_valid_email(email):
        return UpdateEmailResponse(success=False, message="Invalid email address")

    existing_user = get_user_by_email(db, email)
    if existing_user and existing_user.id != user.id:
        return UpdateEmailResponse(success=False, message="Email already exists")

    email_namespace_error = validate_email_for_login_namespace(
        db, email, current_user_id=int(user.id)
    )
    if email_namespace_error:
        return UpdateEmailResponse(success=False, message=email_namespace_error)

    setattr(user, "email", email)
    db.commit()
    db.refresh(user)

    return UpdateEmailResponse(
        success=True,
        message="Email updated successfully",
        user=serialize_auth_user(user),
    )


def _lock_user_row_for_preferences_update(db: Session, user_id: int) -> bool:
    """Serialize concurrent preferences PATCHes for one user, in every
    database - mirrors acquire_runtime_key_transition_fence's dual-dialect
    pattern (services/api_keys.py). PostgreSQL/MySQL take a row-level
    ``FOR UPDATE`` lock; SQLite ignores that clause, so a no-op write grabs
    its write lock instead. Held until this transaction commits, so a
    second concurrent PATCH's read-modify-write of the same JSON column
    blocks here instead of reading stale data and silently dropping the
    first request's disjoint fields on its own commit.

    Returns ``False`` when the user no longer exists (deleted between the
    caller resolving the id and this call), the same contract the
    mirrored helper uses - letting the caller turn that into a clean 404
    instead of an unhandled ``ObjectDeletedError`` from the subsequent
    fetch."""
    if db.get_bind().dialect.name == "sqlite":
        db.execute(
            text("UPDATE users SET id = id WHERE id = :user_id"),
            {"user_id": user_id},
        )
        return db.query(User.id).filter(User.id == user_id).first() is not None
    return (
        db.query(User.id).filter(User.id == user_id).with_for_update().first()
        is not None
    )


def _merge_user_preferences_locked(
    user_id: int, updates: Dict[str, Any]
) -> Dict[str, Any] | None:
    """Entirely self-contained: opens and closes its own Session and never
    touches a request-scoped `user`/`db`. This is required, not just
    tidy: the lock wait this is built around is a real, potentially slow
    blocking DB call under contention, so the endpoint runs this whole
    operation through asyncio.to_thread and drains it via
    await_task_settlement (the same primitive run_db_io_cancellation_safe
    wraps, called directly here so the endpoint can still invalidate the
    voice cache off a settled-but-cancelled result before that
    cancellation propagates) - if the request is cancelled (client
    disconnect, timeout) while this worker is mid-transaction, FastAPI
    can tear down the request's own Session concurrently, and the
    contract is exactly "operation must create/use/close its own Session
    and return only detached data" so that race can't happen (see
    db_runtime.py's docstring on it; this mirrors admin_users.py's
    _delete_user_rows_sync).

    Returns the serialized response payload (not the ORM object), built
    BEFORE commit: Session.commit() defaults to expire_on_commit=True, so
    any attribute access after commit would force a fresh SELECT - itself
    a race, since the lock is released the instant commit() returns and a
    concurrent delete could land in that gap. The preferences merge is
    already reflected in the in-memory object the moment it's set, so
    nothing here needs a post-commit read at all.

    Returns ``None`` if the user no longer exists (deleted between the
    caller resolving ``user_id`` and this call)."""
    session_factory = get_session_local()
    worker_db = session_factory()
    try:
        if not _lock_user_row_for_preferences_update(worker_db, user_id):
            return None
        # Loaded fresh, under the lock, in this operation's own session -
        # not whatever `User` row the caller may have loaded earlier,
        # which could be stale (a concurrent PATCH may have committed its
        # own merge while this request was waiting on the lock).
        worker_user = worker_db.get(User, user_id)
        if worker_user is None:
            return None
        current_preferences = _normalized_preferences(worker_user)
        for key, value in updates.items():
            # An explicit `null` is this endpoint's only clear-a-field
            # signal (see UpdatePreferencesRequest's blank-string
            # rejection) - storing a literal `{key: None}` entry instead
            # of deleting the key would have every future read replay a
            # stale explicit-null forever. Note this doesn't apply to
            # `goals: []`: an empty list is itself a valid value (not a
            # clear signal) and is stored as-is, so `null` and `[]` are
            # two different representations of "no goals" that can
            # coexist - harmless today since nothing branches on the
            # distinction, but worth knowing if that ever changes.
            if value is None:
                current_preferences.pop(key, None)
            else:
                current_preferences[key] = value
        setattr(worker_user, "preferences", current_preferences)
        payload = serialize_auth_user(worker_user)
        worker_db.commit()
        return payload
    finally:
        worker_db.close()


@auth_router.patch("/me/preferences", response_model=UpdatePreferencesResponse)
async def update_current_user_preferences(
    request: UpdatePreferencesRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UpdatePreferencesResponse:
    """Merge the given fields into the current user's stored preferences.
    Each onboarding step (About you, Goals, Launch) calls this with only
    its own fields - a merge, not a replace, so an earlier step's answer
    survives a later step's PATCH."""
    updates = request.model_dump(exclude_unset=True)
    if not updates:
        return UpdatePreferencesResponse(
            success=True,
            message="Preferences updated successfully",
            user=serialize_auth_user(user),
        )

    # Read before releasing `db` below: Session.rollback() unconditionally
    # expires every object loaded through that session, `user` included
    # (unlike expire_on_commit, this isn't conditional on a session
    # setting), so touching `user.id` after release would force an
    # implicit reload - reacquiring the very connection just released,
    # synchronously on the event loop, defeating the point of releasing
    # it at all, and raising ObjectDeletedError outright if the row was
    # deleted concurrently in the meantime.
    user_id = int(user.id)

    # `db` is declared only to release it here, not to do any work with it
    # directly: FastAPI's per-request dependency caching means this is the
    # same (read-only, since get_current_user only did a SELECT) session
    # get_current_user already used, and _merge_user_preferences_locked
    # is about to open a second, independent session and block on a real
    # row lock - without this, that session would sit idle-in-transaction,
    # holding a pool slot, for the whole lock wait (issue #889, same
    # pattern already used before chat.py's sandbox startup and
    # workforce_creator.py's ReAct builder call). A read-only session
    # should always release cleanly; treating a failure as a hard error
    # (mirroring workforce_creator.py's own release_db_connection_if_clean
    # call) surfaces that as a signal instead of silently holding the
    # connection through the lock wait anyway.
    if not release_db_connection_if_clean(db):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "preferences_update_unavailable",
                "message": "Could not release the database before updating preferences.",
            },
        )

    # run_db_io_cancellation_safe's own contract discards a settled result
    # in favor of re-raising the caller's deferred cancellation (see its
    # docstring), which would otherwise skip the cache invalidation below
    # entirely - the merge already committed by the time that cancellation
    # is observed, so the cache must still be invalidated before this
    # function lets that cancellation propagate. propagate_deferred_
    # cancellation (the same helper task_command_transport.py's heartbeat
    # settlement uses) makes that ordering safe even if invalidation
    # itself raises, or 404s below: cancellation always wins over a
    # later exception or return, never the reverse.
    worker = asyncio.get_running_loop().create_task(
        asyncio.to_thread(_merge_user_preferences_locked, user_id, updates)
    )
    serialized_user, cancellation = await await_task_settlement(worker)
    with propagate_deferred_cancellation(cancellation):
        if serialized_user is None:
            raise HTTPException(status_code=404, detail="User not found")

        if "voice" in updates:
            # A cached AgentService bakes voice into its system prompt at
            # construction time and won't re-check preferences on later
            # turns (see invalidate_cached_agents_for_owner's docstring),
            # so an already-warm task would otherwise keep speaking in the
            # old (or now-cleared) voice until incidental eviction/rebuild.
            #
            # The merge above already committed - this is best-effort
            # follow-up, not part of "did the write succeed." An
            # unguarded raise here would turn a successful PATCH into a
            # client-visible 500 for a write that already happened,
            # mirroring the exact bug _run_post_commit_oauth_side_effects
            # (issue #1150) exists to prevent; same fix, same reasoning.
            from ..services.agent_service_manager import get_agent_manager

            try:
                await get_agent_manager().invalidate_cached_agents_for_owner(user_id)
            except Exception:
                logger.warning(
                    "Voice cache invalidation failed for user %s after a "
                    "successful preferences write; a warm task may keep "
                    "speaking in the old voice until incidental eviction",
                    user_id,
                    exc_info=True,
                )

        return UpdatePreferencesResponse(
            success=True,
            message="Preferences updated successfully",
            user=serialized_user,
        )


def _prepare_refresh_response(
    user: User | None, request: RefreshTokenRequest
) -> RefreshTokenResponse:
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    token = getattr(user, "refresh_token", None)
    expires = getattr(user, "refresh_token_expires_at", None)
    if token != request.refresh_token or expires is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    now = datetime.now(timezone.utc)
    if getattr(expires, "tzinfo", None) is None:
        now = now.replace(tzinfo=None)
    if expires < now:
        raise HTTPException(status_code=401, detail="Refresh token has expired")
    identity = {"sub": user.username, "user_id": user.id}
    access_token = create_access_token(
        data=identity, expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    new_token = create_refresh_token(data=identity)
    return RefreshTokenResponse(
        success=True,
        message="Token refreshed successfully",
        access_token=access_token,
        refresh_token=new_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    )


def _refresh_in_worker(
    request: RefreshTokenRequest,
    user_id: Any,
    session_factory: SyncAuthSessionFactory,
) -> RefreshTokenResponse:
    with session_factory() as session:
        user = session.query(User).filter(User.id == user_id).first()
        response = _prepare_refresh_response(user, request)
        # End the read snapshot before competing for a write. In SQLite this
        # avoids a deferred read-to-write lock upgrade; the conditional UPDATE
        # below, not the preceding SELECT, is the authority on token consumption.
        session.rollback()
        now = datetime.now(timezone.utc)
        changed = (
            session.query(User)
            .filter(
                User.id == user_id,
                User.refresh_token == request.refresh_token,
                User.refresh_token_expires_at >= now,
            )
            .update(
                {
                    User.refresh_token: response.refresh_token,
                    User.refresh_token_expires_at: now
                    + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
                },
                synchronize_session=False,
            )
        )
        if changed != 1:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        session.commit()
        return response


@auth_router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_token(
    request: RefreshTokenRequest,
    db: SyncAuthSessionFactory = Depends(get_auth_db),
) -> RefreshTokenResponse:
    """Refresh tokens without sharing a synchronous Session across threads."""
    try:
        payload = verify_refresh_token(request.refresh_token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        user_id = payload.get("user_id")
        return await run_auth_db_worker(
            "refresh_database_transaction",
            lambda: _refresh_in_worker(request, user_id, db),
        )
    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Token refresh failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token refresh is temporarily unavailable",
        )


@auth_router.get("/check")
async def check_auth() -> Dict[str, Any]:
    """Check authentication status endpoint"""
    return {"success": True, "message": "Authentication API is working"}


@auth_router.get("/verify")
async def verify_current_token(
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Verify current token validity"""
    return {
        "success": True,
        "message": "Token is valid",
        "user": serialize_auth_user(current_user),
    }
