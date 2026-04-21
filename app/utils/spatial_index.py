from shapely.strtree import STRtree
from shapely.geometry import shape, Point
from typing import List, Dict, Any

class SpatialIndex:
    """
    Wrapper para indexar features GeoJSON y realizar búsquedas espaciales eficientes.
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
        matches = self.strtree.query(point)
        for geom in matches:
            idx = self.geoms.index(geom)
            key = self.idx_to_key[idx]
            feat = self.features[key]
            if point.within(geom):
                return feat['properties'].get(self.prop_name)
        return None
