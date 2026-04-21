import math

def clean_json(obj):
    """
    Limpia valores NaN, infinitos y otros valores no compatibles con JSON.
    Uso recomendado: serialización de respuestas API.
    """
    if isinstance(obj, dict):
        return {key: clean_json(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [clean_json(item) for item in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj
