# SISTEMA_DE_SEMAFOROS_BACKEND

API FastAPI educativa para la maqueta de dos semáforos.

## Configuración

Define `SEMAPHORE_ADMIN_PASSWORD`, `SEMAPHORE_DEVICE_TOKEN` y
`SEMAPHORE_FRONTEND_ORIGINS` como variables de entorno. No guardes contraseñas,
tokens ni datos de Wi-Fi en el repositorio.

Render debe ejecutar desde el directorio `backend`:

```sh
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Funciones

- `POST /api/admin/login` inicia una sesión de administrador.
- `PUT /api/mode` y `PUT /api/semaphores/{id}` requieren sesión de administrador.
- `PUT /api/timing` ajusta `all_red_seconds` de 1 a 60, solo en modo manual y
  con sesión de administrador.
- `GET /api/state` incluye el tiempo restante de la fase automática.
- `GET /api/history` devuelve hasta 100 eventos recientes.
- `POST /api/device/sensors` recibe las detecciones del ESP32 y requiere
  `X-Device-Token`.

El estado y el historial se mantienen en memoria y se reinician al reiniciar el
servicio. Esta maqueta no debe usarse para controlar tráfico real.
