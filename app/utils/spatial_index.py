from shapely.strtree import STRtree
from shapely.geometry import shape, Point
from typing import List, Dict, Any
import shapely

# Detectar versión de Shapely para usar la API correcta
_SHAPELY_2 = int(shapely.__version__.split('.')[0]) >= 2

class SpatialIndex:
    """
    Wrapper para indexar features GeoJSON y realizar búsquedas espaciales eficientes.
    Compatible con Shapely 1.x y 2.x.
    """
    def __init__(self, features: Dict[Any, dict], prop_name: str):
        self.features = features
        self.prop_name = prop_name
        self.geoms = []
        self.idx_to_key = []
        for k, feat in features.items():
            try:
                geom = shape(feat['geometry'])
                self.geoms.append(geom)
                self.idx_to_key.append(k)
            except Exception:
                continue
        self.strtree = STRtree(self.geoms)

    def query(self, coordinates: List[float]):
        point = Point(coordinates[0], coordinates[1])
        if _SHAPELY_2:
            # Shapely 2.x: query() devuelve array de índices enteros
            indices = self.strtree.query(point)
            for idx in indices:
                geom = self.geoms[idx]
                if point.within(geom):
                    key = self.idx_to_key[idx]
                    feat = self.features[key]
                    return feat['properties'].get(self.prop_name)
        else:
            # Shapely 1.x: query() devuelve lista de geometrías
            for geom in self.strtree.query(point):
                if point.within(geom):
                    idx = self.geoms.index(geom)
                    key = self.idx_to_key[idx]
                    feat = self.features[key]
                    return feat['properties'].get(self.prop_name)
        return None
