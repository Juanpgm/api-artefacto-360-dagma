from pydantic import BaseModel, validator, root_validator
from typing import List

class CoordinatesModel(BaseModel):
    # geometry_type MUST be declared before coordinates: Pydantic validates
    # fields in declaration order, and the coordinates validator reads
    # geometry_type from `values` — so it has to be validated first, otherwise
    # the Point range checks are silently skipped (Pydantic v2).
    geometry_type: str
    coordinates: List[float]

    @validator('coordinates')
    def validate_coordinates(cls, v, values):
        geometry_type = values.get('geometry_type')
        if geometry_type == "Point":
            if len(v) != 2:
                raise ValueError("Point debe tener exactamente 2 coordenadas [lon, lat]")
            lon, lat = v
            if not (-180 <= lon <= 180):
                raise ValueError(f"Longitud inválida: {lon}. Debe estar entre -180 y 180")
            if not (-90 <= lat <= 90):
                raise ValueError(f"Latitud inválida: {lat}. Debe estar entre -90 y 90")
        # Otros tipos pueden agregarse aquí
        return v

class ArbolModel(BaseModel):
    especie: str
    cantidad: int

    @validator('especie')
    def especie_no_vacia(cls, v):
        if not v or not v.strip():
            raise ValueError("El campo 'especie' debe ser texto no vacío")
        return v.strip()

    @validator('cantidad')
    def cantidad_positiva(cls, v):
        if v <= 0:
            raise ValueError("El campo 'cantidad' debe ser mayor que 0")
        return v

class ArbolesDataModel(BaseModel):
    arboles: List[ArbolModel]

    @root_validator(pre=True)
    def parse_json(cls, values):
        arboles = values.get('arboles')
        if isinstance(arboles, str):
            import json
            arboles = json.loads(arboles)
            values['arboles'] = arboles
        if not isinstance(values['arboles'], list) or len(values['arboles']) == 0:
            raise ValueError("arboles_data debe ser un array JSON con al menos un árbol")
        return values
