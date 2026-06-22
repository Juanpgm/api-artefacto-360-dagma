# Notificaciones e Integraciones Google — DAGMA API

## Arquitectura actual

- **Email**: estrategia DUAL en `gmail_service.py`:
  1. **Gmail API (OAuth2)** como transporte PRIMARIO — requiere `GMAIL_CLIENT_ID`,
     `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_SENDER`.
  2. **SMTP (`smtplib`)** como fallback — requiere `SMTP_HOST`, `SMTP_PORT`,
     `SMTP_USER`, `SMTP_PASSWORD` (App Password de Gmail).
  Con reintentos (backoff exponencial), control de cuota diaria y registro en
  `notifications_log`.
- **Invitaciones de calendario**: Archivo `.ics` adjunto en el correo — compatible con Google Calendar, Outlook, Apple Calendar
- **Calendario institucional DAGMA**: Google Calendar API con service account (sin impersonación) — crea eventos en el calendario compartido del grupo

---

## Entregabilidad (anti-spam) — CRÍTICO

`_build_mime_message` produce SIEMPRE:
- `multipart/alternative` con **text/plain + text/html** (nunca solo HTML).
- Headers `Date` y `Message-ID` (smtplib no los agrega solo).
- `Reply-To` si `REPLY_TO_EMAIL` está configurado.
- `List-Unsubscribe` + `List-Unsubscribe-Post: One-Click` en correos masivos
  (broadcast) — requerido por las reglas de remitentes masivos de Gmail/Yahoo 2024.

El loop de broadcast espacia los envíos (`BROADCAST_PACING_SECONDS`, default 0.4s)
para no gatillar el rate-limit (429) de Gmail.

### Lo que NO se arregla en código (DNS del dominio remitente)

Si los correos llegan a spam pese a lo anterior, el problema es alineación de
autenticación del dominio del `From`:
- **SPF**: `TXT` del dominio debe autorizar al servidor de envío (`include:_spf.google.com` si sale por Google).
- **DKIM**: el proveedor debe firmar con la clave del dominio. Si se envía con
  `From: @dominio-propio` pero autenticando con una cuenta `@gmail.com`, la firma
  NO alinea → spam.
- **DMARC**: `TXT` en `_dmarc.<dominio>` con `p=quarantine` o `p=reject` y alineación
  SPF/DKIM. Sin DMARC alineado, Gmail desconfía del remitente.

Regla de oro: el dominio del `From` (`GMAIL_SENDER`) debe coincidir con el dominio
que firma DKIM. Mezclar dominio propio + cuenta Gmail consumer = spam garantizado.

### Diagnóstico offline

`python scripts/diagnose_email_message.py` construye un mensaje real (sin enviar)
y verifica la estructura MIME + headers. `GET /admin/notifications/health` reporta
transporte primario, dominio remitente y flags de configuración.

---

## Servicios (`app/services/`)

| Archivo | Propósito |
|---------|-----------|
| `gmail_service.py` | Envío de emails vía SMTP + adjunto `.ics` |
| `ical_service.py` | Genera bytes del archivo `.ics` para actividades |
| `calendar_service.py` | Crea/actualiza eventos en el calendario Google de DAGMA |
| `google_credentials.py` | Carga credenciales del service account para Calendar API |

---

## Email — SMTP

### Variables de entorno requeridas

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tu_cuenta@gmail.com
SMTP_PASSWORD=xxxx_xxxx_xxxx_xxxx   # App Password de Gmail (no la contraseña normal)
SMTP_SENDER_NAME=DAGMA Artefacto 360
```

### Cómo activar App Password en Gmail

1. Cuenta Google → Seguridad → Verificación en 2 pasos (debe estar activa)
2. Seguridad → Contraseñas de aplicaciones → Crear → nombre: "DAGMA API"
3. Usar la contraseña de 16 caracteres generada como `SMTP_PASSWORD`

### Flujo de notificaciones

- **`POST /convocar_actividad`** → llama `send_activity_confirmation_email(coordinator_email, actividad_data)` con adjunto `.ics`
- **`POST /asignar_personal_actividad`** → por cada persona, llama `send_assignment_notification_email(person_email, nombre, grupo, actividad_data)` con adjunto `.ics`

Ambas funciones son **no bloqueantes** — si el SMTP falla, el endpoint sigue respondiendo 200 y solo registra un warning en el log.

### Usar otro proveedor SMTP

Funciona con cualquier SMTP:
- **Resend**: `SMTP_HOST=smtp.resend.com`, puerto 587, usuario `resend`, password = API key
- **SendGrid**: `SMTP_HOST=smtp.sendgrid.net`, puerto 587, usuario `apikey`, password = API key
- **Outlook**: `SMTP_HOST=smtp.office365.com`, puerto 587

---

## Invitaciones de Calendario — iCal (.ics)

`app/services/ical_service.py` genera un archivo `.ics` (formato iCalendar estándar) que se adjunta automáticamente a todos los correos de notificación.

El destinatario abre el adjunto → se agrega al calendario de su elección (Google Calendar, Outlook, Apple Calendar).

Incluye:
- Fecha, hora y duración de la actividad
- Punto de encuentro (location)
- Descripción con grupos, líder, teléfono y observaciones
- Recordatorio automático 24 horas antes (`VALARM`)

---

## Calendario Institucional DAGMA — Google Calendar API

El service account crea eventos en el calendario compartido de DAGMA.
**No requiere domain-wide delegation** — solo que el calendario esté compartido con el service account.

### Setup (único, ya configurado)

1. Crear un Google Calendar para DAGMA y compartirlo con el service account con permiso "Make changes to events"
2. El Calendar ID ya está hardcodeado en `calendar_service.py`

### Variables de entorno

Solo necesita `FIREBASE_SERVICE_ACCOUNT_JSON` (ya requerida por Firebase).

### Flujo

- Al crear actividad → `create_activity_event(actividad_data, attendee_emails)` — el `calendar_event_id` se guarda en Firestore
- Al asignar personal → `add_attendee_to_event(calendar_event_id, email)` — agrega al evento existente

> Nota: Google Calendar puede enviar invitaciones desde un service account si `sendUpdates='all'`, pero la entrega depende del proveedor del destinatario. El adjunto `.ics` garantiza que el destinatario siempre pueda agregar el evento a su calendario.

---

## GCP Console Setup (Calendar API)

Solo es necesario habilitar la Calendar API en GCP — no se requiere domain-wide delegation:

1. GCP Console → proyecto `dagma-85aad` → APIs & Services → Library
2. Buscar **Google Calendar API** → Enable
3. No se requiere ningún paso adicional en Google Workspace Admin

> La Gmail API SÍ se usa para enviar correos (transporte primario); SMTP es el fallback.
> Para Calendar se usa el service account; para envío de correo se usan credenciales OAuth de Gmail.
