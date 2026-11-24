import os
from enum import Enum
from hmac import compare_digest
from time import monotonic
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

Mode = Literal["manual", "automatic"]
GREEN_SECONDS = 4
YELLOW_SECONDS = 1
ALL_RED_SECONDS = 1


class Color(str, Enum):
    red = "red"
    yellow = "yellow"
    green = "green"


class ColorUpdate(BaseModel):
    color: Color


class ModeUpdate(BaseModel):
    mode: Mode


class SensorReport(BaseModel):
    semaphore1_detected: bool
    semaphore2_detected: bool


app = FastAPI(title="API educativa de semáforos")
frontend_origins = [
    origin.strip()
    for origin in os.getenv(
        "SEMAPHORE_FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type", "X-Device-Token"],
)

semaphore_state: dict[str, Color] = {"1": Color.red, "2": Color.red}
operating_mode: Mode = "manual"
phase = "manual"
phase_started_at = monotonic()
last_green: str | None = None
sensor_detections = {"1": False, "2": False}


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
    return {
        "mode": operating_mode,
        "phase": phase,
        "detected": sensor_detections.copy(),
        "semaphores": {key: value.value for key, value in semaphore_state.items()},
    }


def begin_green(semaphore_id: str, started_at: float) -> None:
    global phase, phase_started_at
    phase = f"green_{semaphore_id}"
    phase_started_at = started_at
    other_id = "2" if semaphore_id == "1" else "1"
    semaphore_state[semaphore_id] = Color.green
    semaphore_state[other_id] = Color.red


def begin_ready(semaphore_id: str, started_at: float) -> None:
    global phase, phase_started_at
    phase = f"ready_{semaphore_id}"
    phase_started_at = started_at
    other_id = "2" if semaphore_id == "1" else "1"
    semaphore_state[semaphore_id] = Color.yellow
    semaphore_state[other_id] = Color.red


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
        return

    if phase.startswith("yellow_") and elapsed >= YELLOW_SECONDS:
        last_green = phase[-1]
        phase = "all_red"
        phase_started_at = now
        semaphore_state["1"] = Color.red
        semaphore_state["2"] = Color.red


@app.get("/api/state")
def read_state() -> dict[str, object]:
    return state_snapshot()


@app.put("/api/mode")
def update_mode(update: ModeUpdate) -> dict[str, object]:
    global operating_mode, phase, phase_started_at, last_green

    if update.mode == operating_mode:
        return state_snapshot()

    operating_mode = update.mode
    if operating_mode == "automatic":
        phase = "all_red"
        phase_started_at = monotonic()
        last_green = None
        semaphore_state["1"] = Color.red
        semaphore_state["2"] = Color.red
    else:
        phase = "manual"
    return state_snapshot()


@app.put("/api/semaphores/{semaphore_id}")
def update_semaphore(semaphore_id: str, update: ColorUpdate) -> dict[str, str]:
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
    semaphore_state[semaphore_id] = update.color
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
    sensor_detections = {
        "1": report.semaphore1_detected,
        "2": report.semaphore2_detected,
    }
    if operating_mode == "automatic":
        advance_automatic(monotonic())
    return state_snapshot()
