# GeoUbicación Bolivia

API (FastAPI) independiente para toda Bolivia que:

1. **Coordenada → zona.** Recibe `lat, lon` y devuelve departamento, municipio, localidad,
   distrito, zona, barrio, calle y dirección.
2. **Zona → polígono.** Recibe el nombre de una zona escrito como lo diría una persona
   ("Sopocachi en La Paz", "UV 45", "Equipetrol") y devuelve su polígono. También dice qué
   coordenadas de una lista caen dentro de ese polígono.
3. **Registra lo que no existe.** Guarda cada nombre buscado que no aparece o que no tiene
   polígono, para saber qué datos faltan.

La API no interpreta frases completas ni guarda datos de quien la usa: recibe una coordenada o
un nombre de zona y responde con datos geográficos. Los límites salen de **polígonos locales**
(`datos/`), no de un servicio externo. Nominatim solo se usa para la calle y la dirección postal,
y si falla la respuesta sale igual.

Hay un mapa de prueba en `/` y la documentación de la API está en `/docs`.

## Datos y cobertura

Las fuentes se combinan en tres capas. Cuando dos fuentes tienen el mismo lugar (mismo nombre y
mismo municipio), **gana la oficial** y la de OSM se descarta al construir los datos.

**1. Límites administrativos, todo el país**

| Nivel | Cantidad | Fuente |
|---|---|---|
| Departamento | 9 | [YoViajo/geodatos](https://github.com/YoViajo/geodatos) |
| Municipio | 339 (INE 2012) | YoViajo/geodatos |
| Localidad (pueblo o ciudad) | 2.941 | **INE, Censo 2024**: manzanos unidos por localidad, vía [mauforonda/atlasurbano](https://github.com/mauforonda/atlasurbano) |

**2. Capas oficiales municipales** ([scripts/fuentes_oficiales.py](scripts/fuentes_oficiales.py))

Son copias de preservación en Archive.org hechas por
[mauforonda/geodatos](https://github.com/mauforonda/geodatos), porque muchos geoservidores
municipales ya no responden.

| Ciudad | Barrios / OTB | Otros niveles | Fuente |
|---|---|---|---|
| La Paz | 709 zonas/OTB 2021 | 179 zonas guía, 10 macrodistritos, 22 distritos, 85 OTB rurales | GAM La Paz (SIT) |
| El Alto | 698 urbanizaciones | 13 distritos | GAM El Alto (GeoBolivia) |
| Cochabamba | 478 OTB | 6 comunas, 14 distritos | GAM Cochabamba, PTDI |
| Trinidad y Beni | 158 barrios | 12 distritos | ENDE / Min. de Hidrocarburos y Energías |
| Cobija | 47 barrios | 6 distritos | GAM Cobija (SISCAT) |
| Camiri | 37 OTB | 9 distritos | GAM Camiri, PTDI |
| Montero | 82 UV | 8 distritos | GAM Montero, PTDI |
| Santa Cruz de la Sierra | 506 UV (sin nombre) | 16 distritos | GAM Santa Cruz vía YoViajo/geodatos |
| Sacaba, Colcapirhua | — | 12 y 5 distritos | GeoBolivia, PTDI |

**3. OpenStreetMap, el resto del país**

~6.200 polígonos de barrios, zonas, condominios y poblados, más ~3.200 barrios que solo existen
como punto. También se usan las zonas compuestas de `datos/alias.json`, por ejemplo
Equipetrol = unión de UV.

**Limitación actual:** en Oruro, Sucre, Tarija y Potosí casi no hay polígonos de barrio. No se
encontraron capas municipales públicas para esas ciudades, y en OSM sus barrios son mayormente
puntos. En esas ciudades el barrio sale de Nominatim, y la localidad, el municipio y el
departamento salen de polígonos. `GET /reportes/cobertura` muestra la situación de cada municipio.

## Puesta en marcha

```bash
pip install -r requirements.txt
python main.py                      # http://localhost:8000  (mapa)  ·  /docs  (Swagger)
python -m pytest tests -q           # tests, sin llamadas a Nominatim
```

Para regenerar `datos/` desde las fuentes. La primera vez descarga unos 110 MB y Overpass puede
tardar varios minutos; con la caché de `fuentes/` tarda alrededor de 1 minuto.

```bash
pip install -r requirements-datos.txt          # numpy, pyarrow, pyproj: solo para construir
python scripts/construir_datos.py              # usa la caché de fuentes/
python scripts/construir_datos.py --refrescar  # descarga todo de nuevo
python scripts/construir_datos.py --sin-osm    # no consulta Overpass (conserva las capas OSM)
```

**Agregar una capa oficial:**
1. Buscar la capa en `descubrir/capas.csv` o `archivar/paquetes.csv` de
   [mauforonda/geodatos](https://github.com/mauforonda/geodatos).
2. Copiar su `archive_item`.
3. Declararla en [scripts/fuentes_oficiales.py](scripts/fuentes_oficiales.py) con su CRS, su
   nivel y el campo que trae el nombre.
4. Volver a construir los datos.

## Endpoints

La documentación interactiva está en **`/docs`**. Resumen:

| Para qué | Ruta |
|---|---|
| ¿En qué zona cae esta coordenada? | `GET /ubicar?lat=&lon=` |
| Lo mismo para hasta 200 coordenadas | `POST /ubicar/lote` |
| Buscar una zona por nombre y obtener su polígono | `GET /zonas/buscar?nombre=` |
| Polígono de una zona ya identificada | `GET /zonas/{id}` |
| ¿Qué coordenadas caen dentro de la zona? | `POST /zonas/{id}/contiene` |
| Zonas buscadas que no existen o no tienen polígono | `GET /reportes/no-encontradas` |
| Cobertura de datos por municipio | `GET /reportes/cobertura` |
| Estado de la API | `GET /estado` |

### Caso de uso: guardar la zona de una coordenada

```
GET /ubicar?lat=-17.7694&lon=-63.1950&calle=false
```

Para cargas masivas, usar `calle=false` o `POST /ubicar/lote`: la zona sale de los polígonos
locales al instante. Con `calle=true` se consulta también Nominatim, que admite una consulta por
segundo. **Guardar también la coordenada**, no solo los nombres: el filtrado posterior se hace
contra el polígono, no comparando nombres.

### Caso de uso: filtrar coordenadas por una zona mencionada por el usuario

Del texto del usuario ("…en equipetrol de 1 habitación…"), el sistema que consume la API extrae
el nombre de la zona. La API solo recibe ese nombre:

```
1)  GET  /zonas/buscar?nombre=equipetrol
      → estado: "encontrado", zona.id_poligono: "alias-equipetrol", zona.poligono: {...}

2)  POST /zonas/alias-equipetrol/contiene
      {"coordenadas": [{"id": "inmueble-101", "lat": -17.7694, "lon": -63.1950}, ...]}
      → total_dentro: 1, dentro: [{"id": "inmueble-101", ...}], fuera: [...]
```

`contiene` acepta hasta 5.000 coordenadas por llamada (5.000 tardan unos 30 ms). Cada
coordenada puede llevar un `id` propio, que se devuelve tal cual. Un punto sobre el borde cuenta
como dentro. Si se prefiere filtrar en la base de datos propia, el paso 1 ya entrega el polígono
en GeoJSON (para PostGIS: `ST_Contains(ST_GeomFromGeoJSON(...), punto)`) y su `bbox` para
prefiltrar.

Antes de filtrar hay que revisar `estado`:

| `estado` | Significado | Qué hacer | ¿Se registra? |
|---|---|---|---|
| `encontrado` | Polígono propio o compuesto | Filtrar con `id_poligono` | No |
| `aproximado` | El barrio es un punto; `id_poligono` es la UV que lo contiene | Filtrar, sabiendo que es aproximado | Sí |
| `sin_poligono` | El barrio existe solo como punto (`zona.punto`); no hay `id_poligono` | Otra estrategia, p. ej. un radio alrededor del punto | Sí |
| `no_encontrado` | Nada supera el umbral; trae `sugerencias` | Pedir otra zona o usar una sugerencia | Sí |

Si `ambiguo` es `true`, hay otra zona con el mismo nombre e igual puntaje en otra ciudad; conviene
repetir la búsqueda indicando la ciudad ("equipetrol santa cruz").

### `GET /ubicar` — detalle de la respuesta

| Parámetro | Por defecto | |
|---|---|---|
| `calle` | `true` | consulta Nominatim para calle y dirección (máx. 1 consulta/s) |
| `incluir_poligono` | `false` | agrega el polígono de la zona más específica |
| `formato` | `geojson` | `geojson` · `lista` · `ambos` |

```json
{
  "status": "success",
  "barrio_real": "Equipetrol",
  "precision_barrio": "poligono",
  "distrito_municipal": "Distrito 1",
  "unidad_vecinal": "UV 33",
  "municipio_ciudad": "Santa Cruz de la Sierra",
  "departamento": "Santa Cruz",
  "sub_divisiones_encontradas": ["Distrito 1", "UV 33", "Equipetrol", "Faremafu", "Piraí"],
  "jerarquia": [{"id": "dep-SC", "nombre": "Santa Cruz", "nivel": "departamento"}, "..."],
  "calle_avenida": "...", "direccion_completa": "..."
}
```

`precision_barrio` indica de dónde sale el barrio. Estas son las fuentes, en orden de prioridad:
1. `poligono`: el punto está dentro del polígono de un barrio. Si hay varios, se prefiere el
   oficial y luego el más chico. Las capas municipales dejan fuera calles y plazas, así que si
   ningún polígono contiene el punto se toma el más cercano del municipio a menos de 45 m; en
   ese caso la distancia va en `distancia_barrio_m`. La misma tolerancia vale para
   macrodistrito, distrito, zona, UV y localidad.
2. `nominatim`: el barrio que Nominatim asigna a esa dirección exacta.
3. `punto_cercano`: el barrio de OSM registrado como punto más cercano, a menos de 600 m o en
   la misma UV. Se usa solo si Nominatim no responde o con `calle=false`. La distancia va en
   `distancia_barrio_m`.
4. `sin_datos`: no hay barrio para ese punto; se registra como `coordenada_sin_barrio`.

`localidad` es el pueblo o la ciudad del punto, como "El Paso" en Quillacollo. Sale de un
polígono, de Nominatim o del poblado más cercano, en ese orden; `precision_localidad` indica
cuál se usó.

Una coordenada fuera de Bolivia devuelve `status: "fuera_de_bolivia"` y también se registra.

### `GET /zonas/buscar` — cómo se interpreta el nombre

- Separa el lugar que acota ("en La Paz", "cochabamba", "scz").
- Quita el relleno ("barrio", "zona", "dame el polígono de…").
- Compara sin tildes y tolerando errores de escritura.

A igual coincidencia, gana en este orden:
1. Por tipo de lugar: municipio, luego zona, luego barrio y, al final, condominio.
2. El que está en el municipio más poblado.
3. El oficial, y luego el que tiene polígono.

**Formato `lista`:** siempre es una lista de partes, y cada parte es su anillo exterior como
`[[lat, lon], ...]`. Así el consumidor no tiene que distinguir Polygon de MultiPolygon. Los
huecos interiores solo van en `geojson`.

### Rutas anteriores (v4.0)

Las rutas anteriores siguen respondiendo igual para no romper integraciones existentes, pero ya
no aparecen en `/docs`. Conviene migrar a las nuevas:

| Anterior | Nueva |
|---|---|
| `GET /ubicacion?lat=&lon=` y `POST /ubicacion` | `GET /ubicar?lat=&lon=` |
| `POST /ubicaciones-lote` | `POST /ubicar/lote` |
| `GET /zona?q=` | `GET /zonas/buscar?nombre=` |
| `GET /zona/{id}` | `GET /zonas/{id}` |
| `GET /registro/no-encontradas` | `GET /reportes/no-encontradas` |
| `GET /cobertura` | `GET /reportes/cobertura` |
| `GET /salud` | `GET /estado` |

## Registro de zonas no encontradas

Cada evento es una línea JSON con `fecha, tipo, consulta` y detalles. Tipos: `no_encontrada`,
`sin_poligono`, `aproximado`, `coordenada_sin_barrio`, `fuera_de_bolivia`.

| Entorno | Dónde queda |
|---|---|
| Local | `registro/zonas_no_encontradas.jsonl` (ruta configurable con `REGISTRO_ZONAS`) |
| Vercel | Logs de la función: buscar `ZONA_NO_ENCONTRADA` (el disco es de solo lectura) |
| Cualquiera | POST al webhook `WEBHOOK_ZONAS_NO_ENCONTRADAS` si está definido (cualquier servicio que reciba un POST JSON) |

## Ampliar la cobertura

- **Nombre popular o sinónimo:** `datos/alias.json` → `nombres_populares` (por municipio).
- **Barrio formado por varias zonas:** `datos/alias.json` → `compuestas`. Se arma con `zonas`
  (ids), `uvs` (códigos de UV de Santa Cruz) o `desde_nombres` (barrios del municipio; si son
  puntos, se usa la UV que los contiene). Ver el ejemplo de Equipetrol.
- **Nueva capa de polígonos** (catastro municipal, INE): agregar su descarga en
  `scripts/construir_datos.py` con el mismo esquema de propiedades
  (`id, nombre, nivel, municipio, provincia, departamento, fuente`) y sumarla a `CAPAS` en
  `app/motor.py`. Candidatas: `mauforonda/atlasurbano` (manzanos del Censo 2024 de todo el
  país) y `mauforonda/geodatos` (respaldo de GeoBolivia).

## Despliegue en Vercel

La configuración está en `vercel.json` y no requiere nada en el panel de Vercel:

1. En Vercel: **Add New → Project** → importar el repositorio de GitHub.
2. **Framework Preset:** dejarlo como venga. Como `vercel.json` usa `builds`, Vercel ignora el
   preset y los comandos del panel (lo avisa con un mensaje en el log de construcción, y es
   lo esperado).
3. **Root Directory:** la carpeta donde está `vercel.json`, es decir, la raíz del repositorio.
4. Variables de entorno (opcionales): `WEBHOOK_ZONAS_NO_ENCONTRADAS` y `NOMINATIM_USER_AGENT`.
5. Deploy. Para comprobarlo: `https://<proyecto>.vercel.app/estado` debe responder `{"estado": "ok", ...}`.

Cada `git push` a `main` vuelve a desplegar. La función pesa unos 60 MB (datos + dependencias),
arranca en ~2,5 s y usa ~160 MB de memoria.

**No cambiar `builds`/`routes` por `functions`/`rewrites`.** Con `functions` + `rewrites`, Vercel
detecta FastAPI por el `main.py` de la raíz y sirve la app desde ahí, pero el `rewrite` le pasa
todas las peticiones con la ruta `/api/index.py`. FastAPI responde entonces 404
(`{"detail":"Not Found"}`) a todo, incluso a `/docs`. Pasó en el primer despliegue de
*barrios-bolivia*.

**Carpeta `.vercel/`** (no se versiona): la vincula `vercel link` o el primer `vercel` de la CLI.
Si se copió el proyecto desde otra carpeta, puede apuntar a otro proyecto o a otra cuenta;
borrarla y volver a vincular antes de usar la CLI.

## Problemas conocidos y acciones pendientes

### Los ids de algunas zonas oficiales pueden cambiar al actualizar la fuente

**Situación.** Las zonas de `oficiales.geojson` y de las capas de Santa Cruz arman su `id` a
partir de la **posición del elemento en el archivo de origen**, por ejemplo `lpz-otb-131`
(elemento 131 de la capa de OTB de La Paz) o `scz-uv-1-33-5387` (incluye el `objectid` del
GAM). Mientras la fuente no cambie, los ids son estables: las capas oficiales se leen de copias
fijas en Archive.org.

**Cuándo falla.** Si se actualiza una capa (otro paquete de Archive.org en
`scripts/fuentes_oficiales.py`, `--refrescar` con una fuente que cambió, o una fuente que
reordena sus elementos), el mismo barrio puede quedar con otro `id`, y otro barrio heredar el
suyo. Todo `id` guardado fuera de la API (por ejemplo, en la base que registra la zona de cada
coordenada) apuntaría a una zona equivocada **sin dar ningún error**. Los ids de OSM
(`osm-w123`), de localidades del INE (`ine-<municipio>-<nombre>`) y de zonas compuestas
(`alias-equipetrol`) no dependen del orden y no tienen este problema.

**Cómo evitarlo hoy.** No guardar el `id` como referencia permanente. Guardar la coordenada y
los nombres, y resolver la zona con `/ubicar` o filtrar con `/zonas/buscar` + `/contiene` en el
momento. Si se necesita el `id`, pedirlo de nuevo cuando se vaya a usar.

**Acción a tomar si se necesitan ids permanentes.** En `scripts/construir_datos.py`
(`construir_oficiales`), armar el id con el **código propio de cada capa** en lugar del índice:
- Declarar en cada fuente de `scripts/fuentes_oficiales.py` un campo `codigo`, una función
  `props -> código único`. Por ejemplo, `cod_zona` en La Paz, `codigo` o `gid_urb` en El Alto,
  y `otb` + `distrito` en Cochabamba cuando la capa no trae código.
- Usar el índice solo como último recurso y avisar con un `print` qué capas lo usan.
- Tras el cambio, publicar una tabla `id anterior → id nuevo` para migrar lo guardado, y
  comprobar que no hay ids duplicados dentro de cada capa (hoy no se valida).

## Estructura

```
app/        main.py (API) · motor.py (espacial + búsqueda) · texto.py · registro.py · nominatim.py · static/
datos/      GeoJSON generados + alias.json (se versionan y se despliegan)
scripts/    construir_datos.py
tests/      test_api.py
api/        index.py — entrada de Vercel, solo importa app.main
_anterior/  versión previa del código, como referencia
```
