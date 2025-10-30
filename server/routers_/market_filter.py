from fastapi import APIRouter, HTTPException, Query
from database import SessionLocal
from sqlalchemy import text
from typing import Dict, Optional, List
import logging

# Configure logging for debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/product-prices",
    tags=["Product Prices"]
)

@router.get("/plazas")
def get_available_plazas() -> Dict:
    """
    Obtains the list of available marketplaces.
    Useful for populating filters on the frontend.
    """
    db = SessionLocal()
    try:
        logger.info("Obteniendo lista de plazas disponibles")
        
        query = """
            SELECT 
                plaza_id,
                nombre,
                ciudad,
                estado
            FROM plazas_mercado
            WHERE estado = 'activa'
            ORDER BY nombre
        """
        result = db.execute(text(query)).fetchall()
        
        plazas = [
            {
                "plaza_id": row.plaza_id,
                "nombre": row.nombre,
                "ciudad": row.ciudad,
                "estado": row.estado
            }
            for row in result
        ]
        
        logger.info(f"Se encontraron {len(plazas)} plazas")
        
        return {
            "total_plazas": len(plazas),
            "plazas": plazas
        }
    except Exception as e:
        logger.error(f"Error al obtener plazas: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al obtener plazas: {str(e)}")
    finally:
        db.close()


@router.get("/")
def get_product_prices(
    product_name: str = Query(..., description="Nombre del producto a buscar"),
    plaza_name: Optional[str] = Query(None, description="Nombre de la plaza de mercado para filtrar (opcional)")
) -> Dict:
    """
    Obtain prices for a product, optionally filtering by location (market).
    
    Scenarios covered:
    1. Filter products in selected location
    2. Search without selecting location (show all)
    3. Filter persistence (handled by frontend with query params)
    """

    db = SessionLocal()
    try:
        logger.info(f"Búsqueda iniciada - Producto: {product_name}, Plaza: {plaza_name}")
        
        # Normalización de nombres (evita errores por espacios, guiones o mayúsculas)
        product_normalized = product_name.replace("-", " ").replace("_", " ").strip()
        plaza_normalized = plaza_name.replace("-", " ").replace("_", " ").strip() if plaza_name else None

        # 🔍 Validar que la plaza existe si se proporcionó un filtro
        if plaza_normalized:
            logger.info(f"Validando plaza: {plaza_normalized}")
            plaza_check_query = """
                SELECT plaza_id, nombre 
                FROM plazas_mercado
                WHERE LOWER(REPLACE(REPLACE(nombre, ' ', ''), '-', '')) =
                      LOWER(REPLACE(REPLACE(:plaza_name, ' ', ''), '-', ''))
                AND estado = 'activa'
            """
            plaza_exists = db.execute(
                text(plaza_check_query), 
                {"plaza_name": plaza_normalized}
            ).fetchone()
            
            if not plaza_exists:
                logger.warning(f"Plaza no encontrada: {plaza_normalized}")
                raise HTTPException(
                    status_code=404,
                    detail=f"La plaza '{plaza_name}' no existe o no está activa. Use el endpoint /product-prices/plazas para ver las plazas disponibles."
                )
            
            logger.info(f"Plaza validada correctamente: {plaza_exists.nombre}")

        # ⚙️ Base query
        base_query = """
            SELECT
                prod.nombre AS producto,
                plz.nombre AS plaza,
                plz.ciudad AS ciudad_plaza,
                p.precio_por_kg,
                p.fecha
            FROM precios AS p
            JOIN productos AS prod ON p.producto_id = prod.producto_id
            JOIN plazas_mercado AS plz ON p.plaza_id = plz.plaza_id
            WHERE LOWER(REPLACE(REPLACE(prod.nombre, ' ', ''), '-', '')) =
                  LOWER(REPLACE(REPLACE(:product_name, ' ', ''), '-', ''))
            AND plz.estado = 'activa'
        """

        params = {"product_name": product_normalized}

        # 🧩 Filtro opcional por plaza
        if plaza_normalized:
            base_query += """
                AND LOWER(REPLACE(REPLACE(plz.nombre, ' ', ''), '-', '')) =
                    LOWER(REPLACE(REPLACE(:plaza_name, ' ', ''), '-', ''))
            """
            params["plaza_name"] = plaza_normalized

        # ✅ Orden final por fecha descendente
        base_query += " ORDER BY p.fecha DESC, plz.nombre ASC"

        logger.info(f"Ejecutando query con params: {params}")
        
        # Ejecutar consulta
        result = db.execute(text(base_query), params).fetchall()
        
        logger.info(f"Resultados encontrados: {len(result)}")

        if not result:
            if plaza_normalized:
                raise HTTPException(
                    status_code=404,
                    detail=f"No se encontraron precios para '{product_name}' en la plaza '{plaza_name}'."
                )
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"No se encontraron precios para '{product_name}' en ninguna plaza registrada."
                )

        # Estructurar respuesta
        prices = [
            {
                "producto": row.producto,
                "plaza": row.plaza,
                "ciudad": row.ciudad_plaza,
                "precio_por_kg": float(row.precio_por_kg),
                "fecha": str(row.fecha)
            }
            for row in result
        ]

        plazas_unicas = sorted(list({r.plaza for r in result}))

        # 📊 Calcular estadísticas por plaza
        precio_por_plaza = {}
        for row in result:
            if row.plaza not in precio_por_plaza:
                precio_por_plaza[row.plaza] = []
            precio_por_plaza[row.plaza].append(float(row.precio_por_kg))
        
        estadisticas_plazas = [
            {
                "plaza": plaza,
                "precio_promedio": round(sum(precios) / len(precios), 2),
                "precio_minimo": min(precios),
                "precio_maximo": max(precios),
                "registros": len(precios)
            }
            for plaza, precios in precio_por_plaza.items()
        ]
        
        logger.info(f"Respuesta generada exitosamente")

        return {
            "producto": product_normalized,
            "filtro_aplicado": {
                "plaza": plaza_name if plaza_name else None,
                "descripcion": f"Resultados filtrados por: {plaza_name}" if plaza_name else "Mostrando todas las plazas disponibles"
            },
            "total_resultados": len(prices),
            "plazas_incluidas": plazas_unicas,
            "estadisticas_por_plaza": sorted(estadisticas_plazas, key=lambda x: x["precio_promedio"]),
            "resultados": prices
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error inesperado: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")
    finally:
        db.close()


@router.get("/compare")
def compare_prices_across_plazas(
    product_name: str = Query(..., description="Nombre del producto a comparar")
) -> Dict:
    """
    Endpoint adicional para comparar precios del mismo producto entre todas las plazas.
    Útil para mostrar diferencias de precios.
    """
    db = SessionLocal()
    try:
        logger.info(f"Comparando precios para producto: {product_name}")
        
        product_normalized = product_name.replace("-", " ").replace("_", " ").strip()
        
        query = """
            SELECT
                plz.nombre AS plaza,
                plz.ciudad,
                AVG(p.precio_por_kg) AS precio_promedio,
                MIN(p.precio_por_kg) AS precio_minimo,
                MAX(p.precio_por_kg) AS precio_maximo,
                COUNT(*) AS num_registros,
                MAX(p.fecha) AS fecha_mas_reciente
            FROM precios AS p
            JOIN productos AS prod ON p.producto_id = prod.producto_id
            JOIN plazas_mercado AS plz ON p.plaza_id = plz.plaza_id
            WHERE LOWER(REPLACE(REPLACE(prod.nombre, ' ', ''), '-', '')) =
                  LOWER(REPLACE(REPLACE(:product_name, ' ', ''), '-', ''))
            AND plz.estado = 'activa'
            GROUP BY plz.plaza_id, plz.nombre, plz.ciudad
            ORDER BY precio_promedio ASC
        """
        
        result = db.execute(text(query), {"product_name": product_normalized}).fetchall()
        
        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontraron precios para '{product_name}' en ninguna plaza."
            )
        
        comparacion = [
            {
                "plaza": row.plaza,
                "ciudad": row.ciudad,
                "precio_promedio": round(float(row.precio_promedio), 2),
                "precio_minimo": float(row.precio_minimo),
                "precio_maximo": float(row.precio_maximo),
                "num_registros": row.num_registros,
                "fecha_mas_reciente": str(row.fecha_mas_reciente)
            }
            for row in result
        ]
        
        # Encontrar mejor y peor precio
        mejor_precio = min(comparacion, key=lambda x: x["precio_promedio"])
        peor_precio = max(comparacion, key=lambda x: x["precio_promedio"])
        diferencia = round(peor_precio["precio_promedio"] - mejor_precio["precio_promedio"], 2)
        porcentaje_diferencia = round((diferencia / mejor_precio["precio_promedio"]) * 100, 2)
        
        logger.info(f"Comparación completada: {len(comparacion)} plazas")
        
        return {
            "producto": product_normalized,
            "total_plazas": len(comparacion),
            "analisis": {
                "plaza_mas_economica": mejor_precio["plaza"],
                "precio_mas_bajo": mejor_precio["precio_promedio"],
                "plaza_mas_costosa": peor_precio["plaza"],
                "precio_mas_alto": peor_precio["precio_promedio"],
                "diferencia_absoluta": diferencia,
                "diferencia_porcentual": f"{porcentaje_diferencia}%"
            },
            "comparacion_por_plaza": comparacion
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en comparación: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error al comparar precios: {str(e)}")
    finally:
        db.close()