import os
import secrets
from enum import Enum
from hmac import compare_digest
from math import ceil
from time import monotonic
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

Mode = Literal["manual", "automatic"]
GREEN_SECONDS = 4
YELLOW_SECONDS = 1
ALL_RED_SECONDS = 1
MIN_ALL_RED_SECONDS = 1
MAX_ALL_RED_SECONDS = 60
MAX_HISTORY_EVENTS = 100
ADMIN_TOKEN_TTL_SECONDS = 12 * 60 * 60


class Color(str, Enum):
    red = "red"
    yellow = "yellow"
    green = "green"


class ColorUpdate(BaseModel):
    color: Color


class ModeUpdate(BaseModel):
    mode: Mode


class TimingUpdate(BaseModel):
    all_red_seconds: int = Field(ge=MIN_ALL_RED_SECONDS, le=MAX_ALL_RED_SECONDS)


class AdminLogin(BaseModel):
    password: str


class SensorReport(BaseModel):
    semaphore1_detected: bool
    semaphore2_detected: bool


app = FastAPI(title="API educativa de semáforos")
default_frontend_origins = [
    "https://semaforos-inteligentes-fr-ix8lner.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
configured_frontend_origins = [
    origin.strip()
    for origin in os.getenv("SEMAPHORE_FRONTEND_ORIGINS", "").split(",")
    if origin.strip()
]
frontend_origins = list(
    dict.fromkeys(default_frontend_origins + configured_frontend_origins)
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_origin_regex=r"^https://semaforos-inteligentes-fr(?:-[a-z0-9-]+)?\.vercel\.app$",
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type", "X-Device-Token"],
)

semaphore_state: dict[str, Color] = {"1": Color.red, "2": Color.red}
operating_mode: Mode = "manual"
phase = "manual"
phase_started_at = monotonic()
last_green: str | None = None
sensor_detections = {"1": False, "2": False}
detection_counts = {"1": 0, "2": 0}
admin_tokens: dict[str, float] = {}
history: list[dict[str, str | int]] = []
history_event_id = 0


def add_history_event(category: str, message: str) -> None:
    global history_event_id

    history_event_id += 1
    history.append(
        {
            "id": history_event_id,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "category": category,
            "message": message,
        }
    )
    if len(history) > MAX_HISTORY_EVENTS:
        del history[:-MAX_HISTORY_EVENTS]


def require_admin_token(authorization: str | None) -> None:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere iniciar sesión como administrador",
        )

    token = authorization.removeprefix("Bearer ")
    expires_at = admin_tokens.get(token)
    if expires_at is None or expires_at <= monotonic():
        admin_tokens.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión de administrador inválida o vencida",
        )


def require_device_token(token: str | None) -> None:
    expected = os.getenv("SEMAPHORE_DEVICE_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El backend no tiene configurado SEMAPHORE_DEVICE_TOKEN",
        )
    if token is None or not compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de dispositivo inválido",
        )


def state_snapshot() -> dict[str, object]:
    remaining_seconds: int | None = None
    if operating_mode == "automatic":
        duration: int | None
        if phase == "all_red":
            duration = ALL_RED_SECONDS
        elif phase.startswith(("ready_", "yellow_")):
            duration = YELLOW_SECONDS
        elif phase.startswith("green_"):
            semaphore_id = phase[-1]
            other_id = "2" if semaphore_id == "1" else "1"
            if sensor_detections[semaphore_id] and not sensor_detections[other_id]:
                duration = None
            else:
                duration = GREEN_SECONDS
        else:
            duration = None

        if duration is not None:
            remaining_seconds = max(
                0, ceil(duration - (monotonic() - phase_started_at))
            )

    return {
        "mode": operating_mode,
        "phase": phase,
        "detected": sensor_detections.copy(),
        "semaphores": {key: value.value for key, value in semaphore_state.items()},
        "timing": {"all_red_seconds": ALL_RED_SECONDS},
        "remaining_seconds": remaining_seconds,
        "statistics": {
            "total_detections": sum(detection_counts.values()),
            "by_semaphore": detection_counts.copy(),
        },
    }


def begin_green(semaphore_id: str, started_at: float) -> None:
    global phase, phase_started_at
    phase = f"green_{semaphore_id}"
    phase_started_at = started_at
    other_id = "2" if semaphore_id == "1" else "1"
    semaphore_state[semaphore_id] = Color.green
    semaphore_state[other_id] = Color.red
    add_history_event("automatic", f"Semáforo {semaphore_id}: verde")


def begin_ready(semaphore_id: str, started_at: float) -> None:
    global phase, phase_started_at
    phase = f"ready_{semaphore_id}"
    phase_started_at = started_at
    other_id = "2" if semaphore_id == "1" else "1"
    semaphore_state[semaphore_id] = Color.yellow
    semaphore_state[other_id] = Color.red
    add_history_event("automatic", f"Semáforo {semaphore_id}: amarillo")


def next_green() -> str:
    detected_ids = [key for key, detected in sensor_detections.items() if detected]
    if len(detected_ids) == 1:
        return detected_ids[0]
    if last_green is None:
        return "1"
    return "2" if last_green == "1" else "1"


def advance_automatic(now: float) -> None:
    global phase, phase_started_at, last_green

    elapsed = now - phase_started_at
    if phase == "all_red":
        if elapsed >= ALL_RED_SECONDS:
            begin_ready(next_green(), now)
        return

    if phase.startswith("ready_"):
        if elapsed >= YELLOW_SECONDS:
            semaphore_id = phase[-1]
            detected_ids = [
                key for key, detected in sensor_detections.items() if detected
            ]
            if len(detected_ids) == 1 and detected_ids[0] != semaphore_id:
                phase = "all_red"
                phase_started_at = now
                semaphore_state["1"] = Color.red
                semaphore_state["2"] = Color.red
                add_history_event("automatic", "Ambos semáforos en rojo")
            else:
                begin_green(semaphore_id, now)
        return

    if phase.startswith("green_"):
        semaphore_id = phase[-1]
        other_id = "2" if semaphore_id == "1" else "1"
        if sensor_detections[semaphore_id] and not sensor_detections[other_id]:
            return
        if elapsed >= GREEN_SECONDS:
            phase = f"yellow_{semaphore_id}"
            phase_started_at = now
            semaphore_state[semaphore_id] = Color.yellow
            semaphore_state[other_id] = Color.red
            add_history_event("automatic", f"Semáforo {semaphore_id}: amarillo")
        return

    if phase.startswith("yellow_") and elapsed >= YELLOW_SECONDS:
        last_green = phase[-1]
        phase = "all_red"
        phase_started_at = now
        semaphore_state["1"] = Color.red
        semaphore_state["2"] = Color.red
        add_history_event("automatic", "Ambos semáforos en rojo")


@app.get("/api/state")
def read_state() -> dict[str, object]:
    return state_snapshot()


@app.get("/api/history")
def read_history() -> list[dict[str, str | int]]:
    return list(reversed(history))


@app.post("/api/admin/login")
def admin_login(login: AdminLogin) -> dict[str, str]:
    expected = os.getenv("SEMAPHORE_ADMIN_PASSWORD")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El backend no tiene configurado SEMAPHORE_ADMIN_PASSWORD",
        )
    if not compare_digest(login.password, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Contraseña de administrador incorrecta",
        )

    now = monotonic()
    expired_tokens = [
        token for token, expires_at in admin_tokens.items() if expires_at <= now
    ]
    for token in expired_tokens:
        del admin_tokens[token]

    token = secrets.token_urlsafe(32)
    admin_tokens[token] = now + ADMIN_TOKEN_TTL_SECONDS
    return {"token": token}


@app.put("/api/mode")
def update_mode(
    update: ModeUpdate,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    global operating_mode, phase, phase_started_at, last_green

    require_admin_token(authorization)
    if update.mode == operating_mode:
        return state_snapshot()

    operating_mode = update.mode
    if operating_mode == "automatic":
        phase = "all_red"
        phase_started_at = monotonic()
        last_green = None
        semaphore_state["1"] = Color.red
        semaphore_state["2"] = Color.red
        add_history_event("automatic", "Modo automático: ambos semáforos en rojo")
    else:
        phase = "manual"
        add_history_event("mode", "Modo manual activado")
    return state_snapshot()


@app.put("/api/timing")
def update_timing(
    update: TimingUpdate,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    global ALL_RED_SECONDS

    require_admin_token(authorization)
    if operating_mode != "manual":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cambia a modo manual para ajustar el tiempo de rojo",
        )
    if update.all_red_seconds != ALL_RED_SECONDS:
        ALL_RED_SECONDS = update.all_red_seconds
        add_history_event(
            "settings",
            f"Pausa con ambos en rojo: {ALL_RED_SECONDS} s",
        )
    return state_snapshot()


@app.put("/api/semaphores/{semaphore_id}")
def update_semaphore(
    semaphore_id: str,
    update: ColorUpdate,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    require_admin_token(authorization)
    if semaphore_id not in semaphore_state:
        raise HTTPException(status_code=404, detail="Semáforo no encontrado")
    if operating_mode != "manual":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cambia a modo manual para controlar los colores desde la web",
        )
    other_id = "2" if semaphore_id == "1" else "1"
    if (
        update.color != Color.red
        and semaphore_state[other_id] != Color.red
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"El semáforo {other_id} debe estar en rojo antes de poner "
                f"el semáforo {semaphore_id} en {update.color.value}"
            ),
        )
    if semaphore_state[semaphore_id] != update.color:
        semaphore_state[semaphore_id] = update.color
        add_history_event(
            "manual", f"Semáforo {semaphore_id}: {update.color.value}"
        )
    return {"id": semaphore_id, "color": update.color.value}


@app.get("/api/device/state")
def read_device_state(
    x_device_token: str | None = Header(default=None),
) -> dict[str, object]:
    require_device_token(x_device_token)
    return state_snapshot()


@app.post("/api/device/sensors")
def report_sensors(
    report: SensorReport,
    x_device_token: str | None = Header(default=None),
) -> dict[str, object]:
    global sensor_detections
    require_device_token(x_device_token)
    updated_detections = {
        "1": report.semaphore1_detected,
        "2": report.semaphore2_detected,
    }
    for semaphore_id, detected in updated_detections.items():
        if detected and not sensor_detections[semaphore_id]:
            detection_counts[semaphore_id] += 1
    if updated_detections != sensor_detections:
        detected_ids = [
            f"vía {key}" for key, detected in updated_detections.items() if detected
        ]
        message = (
            f"Vehículo detectado en {', '.join(detected_ids)}"
            if detected_ids
            else "Sin vehículos detectados"
        )
        add_history_event("sensor", message)
    sensor_detections = updated_detections
    if operating_mode == "automatic":
        advance_automatic(monotonic())
    return state_snapshot()
