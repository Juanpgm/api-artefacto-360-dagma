# 📊 Nuevos Endpoints del Dashboard - Documentación

## Resumen de Implementación

Se han implementado **3 nuevos endpoints** para soportar las funcionalidades del Dashboard y el Historial de Reconocimientos:

1. ✅ **GET /grupo-operativo/stats** - Estadísticas (KPIs)
2. ✅ **GET /grupo-operativo/reportes/recent** - Actividad Reciente
3. ✅ **GET /grupo-operativo/reportes** (mejorado) - Historial con Filtros y Paginación

---

## 1. Endpoint de Estadísticas (KPIs)

### 🔵 GET `/grupo-operativo/stats`

Retorna métricas resumidas para mostrar en el Dashboard.

### Respuesta

```json
{
  "success": true,
  "data": {
    "total_visitas_mes": 12,
    "total_pendientes": 0,
    "parques_visitados": 8
  },
  "timestamp": "2026-02-12T10:30:00.000Z"
}
```

### Campos de Respuesta

| Campo               | Tipo     | Descripción                                                                         |
| ------------------- | -------- | ----------------------------------------------------------------------------------- |
| `total_visitas_mes` | `number` | Total de visitas/reconocimientos del mes actual                                     |
| `total_pendientes`  | `number` | Cantidad de visitas pendientes (actualmente 0, implementar según lógica de negocio) |
| `parques_visitados` | `number` | Conteo único de parques visitados (basado en direcciones únicas)                    |

### Ejemplo de Uso (Frontend)

```javascript
// React/Next.js
const fetchStats = async () => {
  const response = await fetch("/grupo-operativo/stats");
  const { data } = await response.json();

  return {
    visitasMes: data.total_visitas_mes,
    pendientes: data.total_pendientes,
    parques: data.parques_visitados,
  };
};
```

### Lógica Implementada

- ✅ Filtra automáticamente reportes del **mes actual**
- ✅ Calcula parques únicos basándose en el campo `direccion`
- ⚠️ `total_pendientes` está fijo en 0 (requiere implementación según modelo de negocio)

---

## 2. Endpoint de Actividad Reciente

### 🔵 GET `/grupo-operativo/reportes/recent`

Obtiene los últimos N reportes para el widget de "Actividad Reciente".

### Parámetros

| Parámetro | Tipo     | Requerido | Default | Rango | Descripción                     |
| --------- | -------- | --------- | ------- | ----- | ------------------------------- |
| `limit`   | `number` | No        | `3`     | 1-10  | Cantidad de reportes a retornar |

### Respuesta

```json
{
  "success": true,
  "data": [
    {
      "id": "abc-123-def-456",
      "tipo_intervencion": "Mantenimiento",
      "descripcion_intervencion": "Poda de árboles",
      "direccion": "Parque San Antonio",
      "observaciones": "Realizado sin novedad",
      "coordinates": {
        "type": "Point",
        "coordinates": [-76.5225, 3.4516]
      },
      "photosUrl": ["https://..."],
      "photos_uploaded": 3,
      "created_at": "2026-02-12T08:00:00.000Z",
      "timestamp": "2026-02-12T08:00:00.000Z"
    }
  ],
  "count": 3,
  "timestamp": "2026-02-12T10:30:00.000Z"
}
```

### Ejemplo de Uso

```javascript
// Obtener últimos 3 reportes (default)
fetch("/grupo-operativo/reportes/recent");

// Obtener últimos 5 reportes
fetch("/grupo-operativo/reportes/recent?limit=5");

// Obtener último reporte
fetch("/grupo-operativo/reportes/recent?limit=1");
```

### React Component Example

```jsx
const ActividadReciente = () => {
  const [reportes, setReportes] = useState([]);

  useEffect(() => {
    fetch("/grupo-operativo/reportes/recent?limit=3")
      .then((res) => res.json())
      .then((data) => setReportes(data.data));
  }, []);

  return (
    <div>
      {reportes.map((reporte) => (
        <div key={reporte.id}>
          <h4>{reporte.tipo_intervencion}</h4>
          <p>{reporte.direccion}</p>
          <time>{new Date(reporte.created_at).toLocaleDateString()}</time>
        </div>
      ))}
    </div>
  );
};
```

---

## 3. Endpoint de Historial con Filtros

### 🔵 GET `/grupo-operativo/reportes`

Consulta el historial completo con capacidades de filtrado y paginación.

### Parámetros de Filtrado

| Parámetro | Tipo     | Requerido | Validación  | Descripción                                       |
| --------- | -------- | --------- | ----------- | ------------------------------------------------- |
| `year`    | `number` | No        | 2020-2100   | Filtrar por año                                   |
| `month`   | `number` | No        | 1-12        | Filtrar por mes (requiere `year`)                 |
| `search`  | `string` | No        | min: 1 char | Búsqueda parcial en dirección, descripción o tipo |
| `type`    | `string` | No        | -           | Filtrar por tipo de intervención (exacto)         |
| `page`    | `number` | No        | >= 1        | Número de página (default: 1)                     |
| `limit`   | `number` | No        | 1-100       | Resultados por página (default: 20)               |

### Respuesta

```json
{
  "success": true,
  "data": [
    {
      "id": "...",
      "tipo_intervencion": "Mantenimiento",
      "descripcion_intervencion": "...",
      "direccion": "...",
      "coordinates": {...},
      "photosUrl": [...],
      "created_at": "..."
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 20,
    "total_items": 45,
    "total_pages": 3,
    "has_next": true,
    "has_prev": false
  },
  "filters": {
    "year": 2024,
    "month": 2,
    "search": null,
    "type": null
  },
  "timestamp": "2026-02-12T10:30:00.000Z"
}
```

### Ejemplos de Uso

#### 1. Obtener Todos los Reportes

```javascript
fetch("/grupo-operativo/reportes");
```

#### 2. Filtrar por Mes y Año

```javascript
// Febrero 2024
fetch("/grupo-operativo/reportes?year=2024&month=2");

// Todo el año 2024
fetch("/grupo-operativo/reportes?year=2024");
```

#### 3. Búsqueda de Texto

```javascript
// Buscar "parque" en dirección, descripción o tipo
fetch("/grupo-operativo/reportes?search=parque");

// Buscar "San Antonio"
fetch("/grupo-operativo/reportes?search=San%20Antonio");
```

#### 4. Filtrar por Tipo de Intervención

```javascript
fetch("/grupo-operativo/reportes?type=Mantenimiento");
```

#### 5. Paginación

```javascript
// Primera página con 10 resultados
fetch("/grupo-operativo/reportes?page=1&limit=10");

// Segunda página
fetch("/grupo-operativo/reportes?page=2&limit=10");
```

#### 6. Filtros Combinados

```javascript
// Febrero 2024, búsqueda "parque", 5 resultados por página
fetch("/grupo-operativo/reportes?year=2024&month=2&search=parque&limit=5");
```

### Implementación de Filtros (Frontend)

```typescript
interface FilterParams {
  year?: number;
  month?: number;
  search?: string;
  type?: string;
  page?: number;
  limit?: number;
}

const fetchReportes = async (filters: FilterParams) => {
  const params = new URLSearchParams();

  if (filters.year) params.set("year", filters.year.toString());
  if (filters.month) params.set("month", filters.month.toString());
  if (filters.search) params.set("search", filters.search);
  if (filters.type) params.set("type", filters.type);
  if (filters.page) params.set("page", filters.page.toString());
  if (filters.limit) params.set("limit", filters.limit.toString());

  const response = await fetch(`/grupo-operativo/reportes?${params}`);
  return response.json();
};
```

### Paginación Component (React)

```jsx
const Pagination = ({ pagination, onPageChange }) => {
  return (
    <div>
      <button
        disabled={!pagination.has_prev}
        onClick={() => onPageChange(pagination.page - 1)}
      >
        Anterior
      </button>

      <span>
        Página {pagination.page} de {pagination.total_pages}(
        {pagination.total_items} resultados)
      </span>

      <button
        disabled={!pagination.has_next}
        onClick={() => onPageChange(pagination.page + 1)}
      >
        Siguiente
      </button>
    </div>
  );
};
```

---

## Notas de Implementación

### Filtros de Firebase

- ✅ **Año/Mes**: Se implementan como queries de Firestore usando `where()` con rangos de fechas
- ✅ **Tipo**: Se implementa como query exacto de Firestore con `where('tipo_intervencion', '==', type)`
- ⚠️ **Search**: Se implementa **en memoria** ya que Firestore no soporta búsqueda de texto parcial nativamente
  - Se filtran los documentos después de obtenerlos de Firebase
  - Busca en: `direccion`, `descripcion_intervencion`, `tipo_intervencion`
  - Es case-insensitive

### Ordenamiento

Todos los endpoints retornan los reportes ordenados por **fecha de creación descendente** (más recientes primero).

### Paginación

- Se calcula **en memoria** después de aplicar todos los filtros
- Incluye metadatos completos: `page`, `limit`, `total_items`, `total_pages`, `has_next`, `has_prev`

### Optimizaciones Futuras Recomendadas

1. **Índices de Firestore**: Crear índices compuestos para mejorar performance de queries con múltiples filtros
2. **Búsqueda de Texto**: Considerar integrar Algolia o ElasticSearch para búsqueda de texto avanzada
3. **Caché**: Implementar caché de estadísticas (endpoint `/stats`) para reducir consultas a Firebase
4. **Cursors**: Para colecciones muy grandes, considerar usar cursors de Firestore en lugar de paginación offset

---

## Pruebas

Se ha incluido un script de pruebas completo en `test_new_endpoints.py` que valida:

- ✅ Estructura de respuesta de cada endpoint
- ✅ Validaciones de parámetros
- ✅ Lógica de filtrado
- ✅ Metadatos de paginación
- ✅ Límites y casos extremos

### Ejecutar Pruebas

```bash
# Asegúrate de que el servidor esté corriendo
python run.py

# En otra terminal
python test_new_endpoints.py
```

---

## Cambios en Archivos

### Modificados

- ✅ `app/routes/artefacto_360_routes.py`
  - Agregado endpoint `/grupo-operativo/stats`
  - Agregado endpoint `/grupo-operativo/reportes/recent`
  - Mejorado endpoint `/grupo-operativo/reportes` con filtros
  - Agregada importación `from firebase_admin import firestore`
  - Actualizada numeración de endpoints (ENDPOINT 3, 4, 5, 6)

### Creados

- ✅ `test_new_endpoints.py` - Script de pruebas automatizadas
- ✅ `NUEVOS_ENDPOINTS_DASHBOARD.md` - Esta documentación

---

## Ejemplos de Integración Frontend

### Dashboard Stats Widget

```jsx
const DashboardStats = () => {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    fetch("/grupo-operativo/stats")
      .then((res) => res.json())
      .then((data) => setStats(data.data));
  }, []);

  if (!stats) return <div>Cargando...</div>;

  return (
    <div className="stats-grid">
      <StatCard
        title="Visitas este mes"
        value={stats.total_visitas_mes}
        icon="📊"
      />
      <StatCard title="Pendientes" value={stats.total_pendientes} icon="⏳" />
      <StatCard
        title="Parques visitados"
        value={stats.parques_visitados}
        icon="🌳"
      />
    </div>
  );
};
```

### Historial con Filtros

```jsx
const HistorialReportes = () => {
  const [reportes, setReportes] = useState([]);
  const [pagination, setPagination] = useState(null);
  const [filters, setFilters] = useState({
    year: 2024,
    page: 1,
    limit: 10,
  });

  const fetchData = async () => {
    const params = new URLSearchParams(filters);
    const response = await fetch(`/grupo-operativo/reportes?${params}`);
    const data = await response.json();

    setReportes(data.data);
    setPagination(data.pagination);
  };

  useEffect(() => {
    fetchData();
  }, [filters]);

  return (
    <div>
      {/* Filtros */}
      <FilterBar filters={filters} onChange={setFilters} />

      {/* Lista */}
      <ReportesList reportes={reportes} />

      {/* Paginación */}
      {pagination && (
        <Pagination
          pagination={pagination}
          onPageChange={(page) => setFilters({ ...filters, page })}
        />
      )}
    </div>
  );
};
```

---

## Soporte

Para dudas o problemas con estos endpoints, consulta:

- 📄 Código fuente: `app/routes/artefacto_360_routes.py`
- 🧪 Pruebas: `test_new_endpoints.py`
- 📚 Documentación API: `/docs` (FastAPI Swagger UI)

**Fecha de implementación**: 12 de Febrero, 2026  
**Versión API**: 1.0
