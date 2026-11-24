# SISTEMA_DE_SEMAFOROS_BACKEND

API FastAPI educativa para la maqueta de dos semáforos.

## Configuración

Define `SEMAPHORE_ADMIN_PASSWORD` y `SEMAPHORE_DEVICE_TOKEN` como variables de
entorno. El dominio de Vercel del proyecto y las direcciones locales ya están
permitidos en CORS; usa `SEMAPHORE_FRONTEND_ORIGINS` para permitir otros
orígenes. No guardes contraseñas, tokens ni datos de Wi-Fi en el repositorio.

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
- `GET /api/state` incluye el total de detecciones desde el último reinicio y su
  distribución por semáforo.
- `GET /api/history` devuelve hasta 100 eventos recientes.
- `POST /api/device/sensors` recibe las detecciones del ESP32 y requiere
  `X-Device-Token`.

El estado, las estadísticas y el historial se mantienen en memoria y se
reinician al reiniciar el servicio. Esta maqueta no debe usarse para controlar
tráfico real.
