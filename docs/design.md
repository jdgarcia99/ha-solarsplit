# Diseño: SolarSplit, gestión de excedentes (X10Casa)

Oct 29, 2026 · @Juande

## Objetivo y alcance

Una integración HACS propia (**SolarSplit**, dominio `solarsplit`) sustituye a las parejas de automatizaciones enciende/apaga por un único bucle de control que reparte el excedente fotovoltaico entre cargas ordenadas por prioridad.

**Hace:**

- Calcula cada ciclo el presupuesto de potencia disponible.
- Enciende y apaga cargas por prioridad, con histéresis y tiempos mínimos.
- Respeta condiciones por carga (horas de piscina hechas, `climatizar_casa`, `secar_ropa_bano_piso`…).
- Expone entidades de estado y diagnóstico, incluido el motivo de cada decisión.
- Todo se configura desde la UI (config flow y opciones), sin YAML.

**No hace (se queda en automatizaciones o en otros sistemas):**

- Activar o desactivar inyección cero en el Fronius, ni la lógica de precio PVPC (umbral de 5 céntimos, amanecer). La integración solo **lee** el modo activo.
- La regulación proporcional del termo: la sigue haciendo FreeDS.
- Leer el IoTaWatt: lo hace IoTaWatt Plus. Esta integración consume sus sensores.

## Situación actual

Hay 21 automatizaciones con la etiqueta «Gestión de excedentes». Las de reparto de cargas leen `sensor.meter_export_to_grid` cada una por su cuenta, con umbrales y retardos distintos.

| Carga | Entidad | Encender | Apagar | Condiciones extra |
| --- | --- | --- | --- | --- |
| Depuradora | `switch.depuradora_piscina` | `binary_sensor.auth_piscina` 5 min | autom. propia | horas por temporada, hora de inicio opcional |
| Termo | `switch.termo` | > `excedente_activacion_termo` | < `excedente_apagado_termo` | `freeds_maestro` off, `casa_activada`; modo balance neto horario |
| Aires P0 (Sara, Juande Jr, Annabel) | `climate.ac_*` | > 1500 W 5 min | < 50 W 30 s | depuradora y termo encendidos, `climatizar_casa` |
| Aire P1 (Salón) | `climate.ac_salon` | > 600 W 1 min | autom. propia | igual que P0 |
| Radiador baño | `switch.radiador_bano_piso` | > `excedente_activacion_radiador` 1 min | autom. propia | `secar_ropa_bano_piso`, `casa_activada` |

**Problemas que resuelve la integración:**

- La prioridad está en las condiciones: los aires solo arrancan si depuradora y termo ya están encendidos. Añadir o reordenar una carga obliga a tocar varias automatizaciones.
- Nadie descuenta lo ya asignado. Dos cargas pueden contar el mismo excedente y provocar oscilaciones.
- Los aires P0 y P1 tienen umbrales incoherentes: P1 arranca con 600 W y P0 necesita 1500 W, aunque P0 debería ir antes.
- Los aires P0 se encienden y apagan los tres juntos; no hay reparto fino.
- En inyección cero la exportación es casi 0 y deja de servir como señal; por eso la piscina usa lux y potencia FV con un sondeo cada minuto.

## Modelo de datos

Una entrada principal (config entry) con la configuración global, y una **subentrada por carga** (config subentries, como hacen hoy integraciones core). Así cada carga se añade, edita o borra desde la UI sin tocar las demás.

**Configuración global** (editable en Opciones):

| Parámetro | Ejemplo | Para qué |
| --- | --- | --- |
| Sensor de exportación (W) | `sensor.meter_export_to_grid` | señal principal |
| Sensor de importación (W) | del Pro 3EM | detectar déficit |
| Sensor de potencia FV (W) | `sensor.solarnet_power_photovoltaics` | modo inyección cero |
| Intervalo del ciclo (s) | 5 | ritmo del bucle |
| Margen de seguridad (W) | 100 | nunca repartir el último vatio |
| Ventana de suavizado (s) | 30 | filtrar nubes y picos |
| Entidad de modo inyección cero | `input_boolean.zero_injection` | cambia la estrategia |
| Entidad de balance neto horario | `input_boolean.balance_neto_horario` + sensor | modo alternativo |
| Condición global | `input_boolean.casa_activada` | bloquea todo si está off |

**Configuración por carga:**

| Campo | Tipo | Ejemplo (depuradora) |
| --- | --- | --- |
| Nombre | texto | Depuradora |
| Entidad | switch, climate o input\_boolean | `switch.depuradora_piscina` |
| Acción encender / apagar | según dominio (`switch.turn_on`, `climate.set_hvac_mode: cool`…) | automático por dominio |
| Potencia nominal (W) | número | 750 |
| Sensor de potencia real (W) | opcional | enchufe Shelly |
| Prioridad | entero, 1 = primero | 1 |
| Tiempo mínimo encendida / apagada (min) | números | 15 / 5 |
| Respeta `casa_activada` | sí / no | no |
| Condición de uso | selector de entidad + estado, o plantilla | horas de piscina pendientes |
| Objetivo diario | sensor de horas + entidad con el máximo | `sensor.piscina_on_hoy` < `horas_*_piscina` |
| Respeta encendido manual | sí / no | sí |

Los parámetros que hoy son `input_number` (umbrales del termo, del radiador, horas de piscina) pasan a ser campos de la carga. Los que cambian a menudo según temporada se pueden seguir leyendo de un `input_number` si lo prefieres.

## Algoritmo de control

Motor determinista por prioridades, sin optimizador: cada ciclo calcula un presupuesto y hace como mucho **un cambio**, y luego espera a ver su efecto antes del siguiente.

**Presupuesto disponible** (valores suavizados con media móvil):

```latex
P_{disp} = P_{export} - P_{import} + \sum_{i \in on} P_i - P_{margen}
```

Se suma lo que ya consumen las cargas gestionadas encendidas, porque ese excedente «ya está repartido» y se puede reasignar. Se usa la potencia real si hay sensor; si no, la nominal.

**Ciclo (cada 5 s por defecto):**

1. Si el interruptor maestro está apagado o falla la condición global, no hace nada más (no apaga cargas: las deja como estén).
2. Si hay un cambio reciente en periodo de asentamiento (por defecto 30 s), espera.
3. Recorre las cargas por prioridad y calcula el **estado deseado**: cada carga elegible entra si cabe en lo que queda de presupuesto, y se descuenta su potencia.
4. Compara deseado y real. Elige un solo cambio: primero apagados (de menor a mayor prioridad), luego encendidos (de mayor a menor).
5. Antes de ejecutarlo, comprueba histéresis y tiempos mínimos. Si no se cumplen, registra el motivo y no actúa.

**Histéresis por carga:**

- Encender: el presupuesto debe superar `P_i + 100 W` de forma estable durante el retardo de encendido.
- Apagar: solo si el déficit supera la tolerancia de importación (por defecto 150 W) durante el retardo de apagado.
- Tiempo mínimo encendida y apagada: protege compresores de aire y la bomba de la depuradora.

**Reparto entre motor y coordinador.** `engine.py` es una función pura y aplica todas las reglas de tiempo: asentamiento, tiempos mínimos y retardos de encendido y apagado. El coordinador no decide nada; solo guarda datos entre ciclos:

- Por carga, dos marcas de tiempo: desde cuándo se cumple sin interrupción la condición de encendido (`on_since`, «cumple_on_desde») y la de apagado (`off_since`, «cumple_off_desde»).
- El motor recibe las marcas en el estado de cada carga y devuelve las nuevas en la decisión (`Decision.on_since` / `off_since`). Una carga que no aparece ahí ya no cumple la condición y su marca vuelve a `None`. El coordinador las guarda tal cual y se las pasa en el ciclo siguiente.
- Las calcula el motor porque saber si la condición se cumple depende del recorrido por prioridades (cuánto presupuesto queda al llegar a esa carga); calcularlas fuera duplicaría el algoritmo.
- Si la condición se cumple pero no durante el retardo, el motor no actúa y devuelve el motivo `on_delay` u `off_delay`, igual que `min_on_time` y `min_off_time` para los tiempos mínimos. Las marcas siguen avanzando durante el asentamiento.

**Saltos de prioridad:** si la carga de prioridad 2 no cabe pero la 3 sí, la 3 entra. Configurable por carga con «no saltar», para reproducir la regla actual de que los aires solo arrancan con depuradora y termo encendidos.

**Respeto al control manual:** si una carga cambia de estado sin que la integración lo haya ordenado, pasa a «manual» y se excluye hasta que vuelva a su estado o se reactive su interruptor.

**Modo inyección cero.** La exportación es casi 0 porque el Fronius limita, así que el excedente no se puede medir. Se usa **sondeo**:

1. Si la FV supera el mínimo configurado y no hay importación, prueba a encender la siguiente carga.
2. Tras el asentamiento, si la importación supera la tolerancia, la apaga y espera un tiempo creciente antes de reintentar (5, 10, 20 min).
3. Si no hay importación, el inversor ha subido producción y la carga se queda.

Los umbrales actuales de lux y FV mínima se mantienen como precondición del sondeo.

**Modo balance neto horario.** El presupuesto pasa a ser el balance proyectado al final de la hora: balance acumulado más potencia neta actual por el tiempo que queda. Permite importar un rato si la hora va a cerrar en positivo. Sustituye a los disparadores de `sensor.balance_neto_horario` del termo.

## Entidades y servicios

Todas con la etiqueta «Gestión de excedentes» y agrupadas en un dispositivo global más un dispositivo por carga.

**Dispositivo global:**

| Entidad | Tipo | Contenido |
| --- | --- | --- |
| Gestión activa | switch | interruptor maestro; sustituye a `input_boolean.gestion_de_excedentes` |
| Presupuesto disponible | sensor (W) | `P_disp` del último ciclo |
| Potencia asignada | sensor (W) | suma de cargas gestionadas encendidas |
| Modo | sensor (enum) | normal, inyección cero, balance horario, pausado |
| Última decisión | sensor | texto, p. ej. «encendida Depuradora: 1.240 W disponibles» |

**Por carga:**

| Entidad | Tipo | Contenido |
| --- | --- | --- |
| Gestionar | switch | incluir o excluir la carga sin borrarla |
| Estado | sensor (enum) | encendida, apagada, esperando, bloqueada, manual |
| Motivo | sensor | p. ej. «tiempo mínimo apagada: faltan 3 min», «condición de uso no cumplida» |
| Prioridad | number | editable al vuelo |
| Tiempo encendida hoy | sensor (h) | útil para la depuradora |

**Servicios:**

- `solarsplit.force_on` / `force_off`: fuerza una carga durante N minutos y luego la devuelve al control.
- `solarsplit.recalculate`: ejecuta un ciclo inmediato.

El atributo de diagnóstico de cada ciclo (presupuesto, cargas evaluadas, decisión) va a los diagnósticos descargables de la integración, para suplir las trazas de las automatizaciones.

## Casos especiales

**Termo y FreeDS.** Con `freeds_maestro` encendido, FreeDS regula el termo de forma proporcional y la integración no debe tocarlo. El termo se declara como **carga variable**: no se enciende ni apaga, pero su consumo real cuenta en el presupuesto. Las cargas de menor prioridad solo reciben lo que FreeDS no esté usando, y conviene darle al termo una prioridad alta para que FreeDS se quede el excedente primero. Con `freeds_maestro` apagado, el termo pasa a ser una carga de relé normal con su umbral.

Las dos automatizaciones de FreeDS manual en inyección cero (calentar si baja de 36 °C) no son reparto de excedente sino una regla de confort. Se quedan como automatizaciones.

**Aires.** Hoy P0 enciende tres equipos a la vez. En la integración cada aire es una carga independiente (Sara, Juande Jr, Annabel, Salón) con su potencia, de modo que entran de uno en uno según haya presupuesto. Tiempo mínimo encendido sugerido: 15 min, para no castigar el compresor. Condición de uso: `input_boolean.climatizar_casa`. Acción: `climate.set_hvac_mode` con `cool` u `off`; en invierno se podría usar `heat` con un selector de modo por temporada.

**Depuradora.** Condición de uso = horas pendientes según `input_select.temporada_piscina` y `sensor.piscina_on_hoy`, y hora de inicio opcional. `binary_sensor.auth_piscina` deja de hacer falta: lo sustituye el presupuesto. La automatización de cambio de temporada se queda.

**Inyección cero.** La integración lee `input_boolean.zero_injection` y cambia al modo de sondeo. Las automatizaciones que activan y desactivan la inyección cero (precio, amanecer, auto activación con termo caliente) se quedan fuera, porque son decisiones económicas, no de reparto.

**`casa_activada`.** Es la condición global por defecto, pero cada carga puede ignorarla. La depuradora debe seguir funcionando con la casa deshabitada; el radiador y los aires no.

## Repositorio, pruebas y migración

Mismo flujo que LumaPresence e IoTaWatt Plus: repositorio público en GitHub (`jdgarcia99/ha-solarsplit`), instalación por HACS, desarrollo con Claude Code.

**Estructura propuesta:**

```
ha-solarsplit/
  custom_components/solarsplit/
    __init__.py          # setup, coordinator, servicios
    manifest.json
    config_flow.py       # entrada global + subentradas por carga
    engine.py            # algoritmo puro, sin dependencias de HA
    loads.py             # adaptadores por dominio (switch, climate, variable)
    coordinator.py       # bucle, suavizado, lectura de sensores
    sensor.py  switch.py  number.py
    diagnostics.py
    translations/es.json  en.json
  tests/
    test_engine.py       # casos del algoritmo con datos simulados
    test_config_flow.py
  hacs.json
  README.md
```

La clave es que `engine.py` sea una función pura: recibe mediciones y estado de las cargas y devuelve la decisión con su motivo. Así se prueba con pytest sin Home Assistant, incluido reproducir días reales exportados de InfluxDB.

**Migración por fases:**

1. **Modo sombra.** La integración calcula decisiones pero no actúa; solo actualiza los sensores de estado y motivo. Conviven con las automatizaciones actuales una o dos semanas y se comparan.
2. **Radiador del baño.** La carga de menos riesgo. Se desactivan sus automatizaciones y la integración la controla.
3. **Aires**, uno a uno, con tiempos mínimos ya ajustados.
4. **Depuradora y termo.** Las que más cuentan en la factura y en el agua.
5. **Modo inyección cero** con sondeo, sustituyendo las automatizaciones de piscina en inyección cero.
6. Borrar las automatizaciones sustituidas y los ayudantes que ya no se usen (`auth_piscina`, umbrales).

Cada fase se puede revertir apagando el switch «Gestionar» de la carga y reactivando sus automatizaciones.

**Preguntas abiertas:**

- [ ] Potencia real de cada aire en modo `cool` (¿hay medida por enchufe o por circuito en el IoTaWatt?).
- [x] Nombre definitivo de la integración y del repositorio: **SolarSplit**, `jdgarcia99/ha-solarsplit`.
- [ ] ¿Los aires también deben gestionar `heat` en invierno?
