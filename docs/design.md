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
- La regulación proporcional del termo: la hace el x10derivador (firmware ESPHome propio). SolarSplit solo le escribe la potencia máxima (carga regulable).
- La seguridad del termo (el corte del Shelly Pro 1PM, `switch.termo`): es independiente de SolarSplit y **no debe implementarse en ella**. SolarSplit nunca conmuta `switch.termo`.
- Leer el IoTaWatt: lo hace IoTaWatt Plus. Esta integración consume sus sensores.

## Situación actual

Hay 21 automatizaciones con la etiqueta «Gestión de excedentes». Las de reparto de cargas leen `sensor.meter_export_to_grid` cada una por su cuenta, con umbrales y retardos distintos.

| Carga | Entidad | Encender | Apagar | Condiciones extra |
| --- | --- | --- | --- | --- |
| Depuradora | `switch.depuradora_piscina` | `binary_sensor.auth_piscina` 5 min | autom. propia | horas por temporada, hora de inicio opcional |
| Termo | `switch.termo` | > `excedente_activacion_termo` | < `excedente_apagado_termo` | `freeds_maestro` off, `casa_activada`; modo balance neto horario (antes de 0.2.0: FreeDS; ahora x10derivador, ver «Termo y x10derivador») |
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
| Sensor de red con signo (W) y signo | alternativa a los dos anteriores (0.2.0) | exportación negativa o positiva |
| Sensor de potencia FV (W) | `sensor.solarnet_power_photovoltaics` | modo inyección cero |
| Intervalo del ciclo (s) | 5 | ritmo del bucle |
| Margen de seguridad (W) | 100 | nunca repartir el último vatio |
| Ventana de suavizado (s) | 30 | filtrar nubes y picos |
| Entidad de modo inyección cero | `input_boolean.zero_injection` | cambia la estrategia |
| Entidad de balance neto horario | `input_boolean.balance_neto_horario` + sensor | modo alternativo |
| Condición global | `input_boolean.casa_activada` | bloquea todo si está off |
| Entidad de temporada | `input_select.temporada_piscina` | limita cargas a algunas temporadas (0.2.0) |
| Tiempo de datos congelados (s) | 20 | lectura de red sin informar = desconocida (0.2.0) |

**Configuración por carga:**

| Campo | Tipo | Ejemplo (depuradora) |
| --- | --- | --- |
| Nombre | texto | Depuradora |
| Tipo de carga | relé o regulable (0.2.0) | relé |
| Entidad | switch, climate o input\_boolean | `switch.depuradora_piscina` |
| Acción encender / apagar | según dominio (`switch.turn_on`, `climate.set_hvac_mode: cool`…) | automático por dominio |
| Potencia nominal (W) | número | 750 |
| Sensor de potencia real (W) | opcional, exclusivo de la carga | enchufe Shelly |
| Sensor del circuito (W) | opcional, compartido con otras cargas (0.2.0) | canal del IoTaWatt de los aires |
| Prioridad | entero, 1 = primero | 1 |
| Tiempo mínimo encendida / apagada (min) | números | 15 / 5 |
| Respeta `casa_activada` | sí / no | no |
| Condición de uso | selector de entidad + estado, o plantilla | horas de piscina pendientes |
| Objetivo diario | sensor de horas + entidad con el máximo | `sensor.piscina_on_hoy` < `horas_*_piscina` |
| Respeta encendido manual | sí / no | sí |
| Solo bajo demanda, volver a gestionar automáticamente (y hora), temporadas | sección plegable «Control manual y temporada» (0.2.0) | aires: bajo demanda; depuradora: temporadas |
| Carga regulable: entidad de límite, entidad de modo y valor automático, límite mínimo y máximo, cambio mínimo, tiempo entre escrituras | sección plegable (0.2.0) | termo: `number.coladuria_x10_derivador_potencia_maxima`, `select.coladuria_x10_derivador_modo` = `Auto`, 0–1600 W, 100 W, 30 s |

Los parámetros que hoy son `input_number` (umbrales del termo, del radiador, horas de piscina) pasan a ser campos de la carga. Los que cambian a menudo según temporada se pueden seguir leyendo de un `input_number` si lo prefieres.

## Algoritmo de control

Motor determinista por prioridades, sin optimizador: cada ciclo calcula un presupuesto y hace como mucho **un cambio**, y luego espera a ver su efecto antes del siguiente.

**Presupuesto disponible** (valores suavizados con media móvil):

```latex
P_{disp} = P_{export} - P_{import} + \sum_{i \in on} P_i - P_{margen}
```

Se suma lo que ya consumen las cargas gestionadas encendidas, porque ese excedente «ya está repartido» y se puede reasignar. Se usa la potencia real si hay sensor; si no, la nominal.

**Ciclo (cada 5 s por defecto):**

1. Si el interruptor maestro está apagado, no hace nada más (no apaga cargas: las deja como estén). Si falla la condición global, solo apaga las cargas que encendió la propia integración y que respetan la condición (ver «Decisiones de implementación»); el resto se deja como esté.
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
| Tiempo encendida hoy | sensor (h) | útil para la depuradora (0.2.0) |
| Petición activa | binary_sensor | solo cargas bajo demanda (0.2.0) |
| Potencia aprendida | sensor (W) | solo cargas con sensor del circuito (0.2.0) |

**Servicios:**

- `solarsplit.force_on` / `force_off`: fuerza una carga durante N minutos y luego la devuelve al control.
- `solarsplit.recalculate`: ejecuta un ciclo inmediato.

El atributo de diagnóstico de cada ciclo (presupuesto, cargas evaluadas, decisión) va a los diagnósticos descargables de la integración, para suplir las trazas de las automatizaciones.

### Decisiones de implementación (0.1.0)

- **Modo sombra** es un ajuste global (Opciones), **activado por defecto**: el bucle calcula y publica todo, pero nunca llama a un servicio sobre una carga. Así una instalación nueva no actúa hasta que se desactiva a propósito.
- **Última decisión** es un sensor enum (`none`, `turn_on`, `turn_off`) con atributos `load`, `reason`, `budget` y `shadow`, en lugar de un texto libre: así se traduce en la UI y se puede usar en automatizaciones y gráficas.
- **Motivo** es un sensor enum con los códigos del motor (`no_budget`, `on_delay`, `min_on_time`…), traducidos.
- **Condición global y cargas que la ignoran.** Con la condición global apagada, las cargas de relé que respetan la condición y que encendió SolarSplit (`owned`) se apagan (motivo `global_shutdown`): sin retardo de apagado, porque es una orden deliberada y no una nube, pero respetando el tiempo mínimo encendida. Las encendidas a mano, las apagadas y las variables quedan fuera del reparto tal como estén (motivo `global_condition`) y su consumo no se reasigna; las que ignoran la condición (depuradora) siguen gestionándose. En modo sombra se simula que SolarSplit es dueño de toda carga encendida, para publicar qué haría. El interruptor maestro, en cambio, nunca apaga nada.
- **Cargas no disponibles** (entidad `unavailable`/`unknown`) se tratan igual: se dejan como estén.
- **Datos desconocidos (0.2.0).** Una entidad `unavailable`/`unknown` es un dato desconocido, nunca 0 W.
  - Si falta una lectura de red, el motor recibe `Measurements = None`: no decide, el presupuesto es desconocido, el motivo es `grid_unknown` y las marcas de retardo vuelven a empezar. Ya no se lanza `UpdateFailed`, así que las entidades siguen disponibles y muestran el motivo.
  - **Sensor de red con signo (0.2.0).** En lugar de los sensores de exportación e importación se puede usar uno solo con signo, con un selector que dice si la exportación es negativa (por defecto) o positiva. El coordinador lo convierte en exportación e importación (la parte positiva de cada lado) antes del suavizado: el motor no cambia. El formulario exige un modo u otro, no los dos (error `grid_sensors`); si la entrada tuviera ambos, manda el sensor con signo. Al cambiar de modo en Opciones, los sensores que se vacían se guardan como `None`.
  - **Datos congelados con un solo sensor:** regla simple. Si no informa (`last_reported`) en el «tiempo de datos congelados», es desconocido, valga lo que valga: un sensor con signo no tiene un lado en reposo que pueda callar.
  - **Datos congelados con dos sensores:** se mira `last_reported` de cada sensor de red frente al «tiempo de datos congelados» (20 s; 0 lo desactiva). Un sensor congelado es el que tiene un valor distinto de cero sin informar durante ese tiempo, o los dos sensores sin informar a la vez. Un lado en 0 W puede callar: el medidor de casa solo escribe el lado que cambia (`sensor.meter_import_from_grid` pasa horas en 0 sin informar mientras se exporta), y tratarlo como desconocido pararía el bucle todo el día.
  - Entidad de inyección cero desconocida: el bucle se pausa con motivo `mode_unknown`.
  - Condición global o de uso desconocida: la carga se deja como esté y su consumo no se reasigna (`global_unknown`, `condition_unknown`). No es un apagado global: que una entidad deje de informar no es que la condición falle.
  - Carga regulada fuera con sensor de potencia o entidad de regulación ilegibles: no disponible, se deja como esté.
- **Control manual.** El coordinador recuerda el último estado que ordenó (`commanded`). Si la carga está en otro estado, es «manual» hasta que vuelva a ese estado o se reactive su interruptor Gestionar, que adopta el estado actual.
- **Apagada desde fuera (0.2.0).** Si el coordinador ve una transición de encendida a apagada que no ordenó él (`commanded` no es `False`), marca la carga como `switched_off` en su memoria: queda «bloqueada» con motivo `switched_off` y el motor nunca la vuelve a encender, aunque no respete cambios manuales. Es lo que pasa con un corte de seguridad. La marca se borra cuando la carga vuelve a estar encendida o al reactivar Gestionar. En modo sombra no se detecta, porque todos los apagados los hacen las automatizaciones. Una carga que se apaga con Home Assistant parado también queda bloqueada (antes quedaba manual).
- **Prioridad** (number) se aplica al vuelo y se guarda en la subentrada de la carga. El coordinador guarda una instantánea de la configuración con la que se creó; el listener de actualización solo recarga si la entrada difiere de ella, así que el cambio de prioridad no recarga.
- **Memoria persistente** (`Store` de Home Assistant, `solarsplit.<entry_id>`): por carga, `managed`, `commanded`, las marcas de retardo y la última transición observada (`last_seen`, `last_change`), más el interruptor maestro y la última acción. Se guarda con retardo cada ciclo y de inmediato al descargar la entrada. Al arrancar, las marcas de retardo se descartan si el hueco supera 2 minutos. Los tiempos mínimos usan `last_change` propio y no `last_changed` de Home Assistant, que se reinicia al arrancar. Una carga que cambió con Home Assistant parado queda como manual porque difiere de `commanded`.
- **Etiqueta** «Gestión de excedentes»: se crea si no existe y se pone a todas las entidades al configurar la entrada.
- **Carga regulable (0.2.0).** Tercer tipo de carga, además de relé y variable: un dispositivo que se regula solo (el x10derivador del termo) y del que SolarSplit solo escribe el límite de potencia. Se configura en la subentrada con «Tipo de carga: Regulable» y una sección plegable con entidad de límite (`number`/`input_number`), entidad de modo y su valor automático, límite mínimo y máximo, cambio mínimo que se escribe (100 W) y tiempo mínimo entre escrituras (30 s). El sensor de potencia real es obligatorio. Los campos de la sección se guardan planos en la subentrada.
  - **Solo cuenta en automático.** Con el modo en su valor automático, su consumo real es excedente: entra en el presupuesto como el de cualquier carga gestionada y se reasigna. En otro modo (Manual, Apagado) ese consumo es de red: no se reasigna y la carga queda «externa» (motivo `external`).
  - **Límite por prioridad.** En el recorrido por prioridades, si ninguna carga anterior usa o va a usar presupuesto (desea estar encendida, o es variable o regulable con consumo), su límite es el máximo (`full_power`); así la carga de prioridad más alta queda en su máximo. Si no, el límite es lo que queda del presupuesto al llegar a ella, acotado entre mínimo y máximo (`limited`). Con la condición de uso incumplida, el mínimo. Después se descuenta su consumo real, no su límite: las cargas siguientes reciben lo que no use.
  - **Escrituras.** El motor devuelve el límite deseado (`Decision.limits`) y las escrituras de este ciclo (`Decision.writes`): solo si el cambio llega al umbral o alcanza un extremo, y no antes del intervalo desde la última escritura (`Regulator.written`, que guarda el coordinador). El adaptador redondea hacia abajo al paso de la entidad y lo acota a su rango. Las escrituras no cuentan como la única acción de conmutación del ciclo.
  - **Devolución del máximo.** Si SolarSplit la bajó (`lowered`) y deja de limitarla (pausa o interruptor maestro, Gestionar apagado, otro modo, condición global apagada, interruptor apagado), el motor escribe el máximo en el acto. Al descargar o desactivar la entrada lo hace el coordinador. Solo se devuelve lo que SolarSplit bajó, para no pisar un valor puesto a mano. Con datos de red desconocidos no se escribe nada.
  - **Tras un reinicio.** Antes del primer ciclo, `async_release_on_start` devuelve el máximo a cada carga regulable que esté en un modo distinto del automático, con el límite por debajo del máximo, si la memoria guardada dice que lo bajó SolarSplit (`lowered`). Así un reinicio no deja frenados los refuerzos del termo en Manual. Si el modo o el límite aún no se pueden leer (el derivador no ha conectado), no escribe nada y lo hará el ciclo cuando se puedan leer. Con datos de red desconocidos, el motor también devuelve el máximo a las regulables fuera de automático o no gestionadas: no hace falta la red para eso; las que siguen en automático se dejan como estén. No se devuelve un límite que ya está en su máximo, y el coordinador borra `lowered` en cuanto lo ve en el máximo, lo haya puesto quien lo haya puesto.
  - **Modo sombra.** Nunca escribe el límite, ni para bajarlo ni para devolver el máximo: solo publica el estado, el motivo y el límite que escribiría (atributo `limit`). La comprobación está en el único método que escribe (`_async_write_limit`), así que cubre el ciclo, los interruptores, la descarga y el arranque. Como no escribe, la memoria (`lowered`) tampoco cambia.
  - **Nunca se conmuta.** Su entidad es el interruptor que tiene delante (`switch.termo`): SolarSplit no lo toca; si está apagado, la carga queda bloqueada (`switched_off`). Con potencia, límite o modo ilegibles, no disponible: no se escribe.
- **Termo con FreeDS (0.1.0, sustituido):** la carga tenía un campo «Regulada externamente cuando» (`freeds_maestro`). FreeDS ya no existe; el campo se mantiene para otras cargas variables, y el termo pasa a ser una carga regulable (ver «Termo y x10derivador»).
- **Objetivo diario (0.2.0).** Sección plegable «Objetivo diario» de la subentrada: sensor de horas hoy (opcional; vacío = cuenta propia), entidad con las horas objetivo (`input_number`, `number` o `sensor`) o un valor fijo (0 = sin objetivo). El motor recibe `hours_today`, `target_hours` y `target_done`; si las horas llegan al objetivo, la carga deja de ser elegible (motivo `target_reached`): se apaga respetando el tiempo mínimo encendida y no cuenta como hueco para «no saltar». Una regulable recibe su límite mínimo. El motor devuelve `Decision.target_reached` y el coordinador guarda la fecha local en la memoria: queda cumplido **hasta el día siguiente**, aunque luego suba el objetivo o baje el sensor. Reactivar Gestionar lo borra. Con horas u objetivo ilegibles (y sin cumplir hoy), la condición es desconocida y la carga se deja como esté.
- **Tiempo encendida hoy (0.2.0).** Sensor por carga (h, `total_increasing`) contado por el coordinador a partir del estado real de la carga, la encienda quien la encienda: suma de los tramos cerrados de hoy más el tramo en curso desde la medianoche local como muy pronto. Va en la memoria persistente (`today`, `on_today`), así que sobrevive a reinicios; se pone a cero al cambiar la fecha local. Es la cuenta que usa el objetivo diario si no se indica sensor de horas.
- **Formulario de cargas: secciones sin valor por defecto (0.2.0).** Las secciones se declaran `vol.Required(sección)` sin `default`, como en el núcleo de Home Assistant. Con `default={}` el frontend enviaba la sección vacía en lugar de rellenarla con los valores y sugerencias de sus campos: se veían en blanco y, al editar una carga, se borraban sus entidades opcionales (una regulable ya no se podía guardar). Las pruebas del formulario envían lo que enviaría el frontend.
- **Cargas bajo demanda (0.2.0).** Opción por carga «Solo bajo demanda» (solo relé). El coordinador lleva una **petición** en la memoria persistente (`request`): empieza cuando la carga pasa a encendida sin que SolarSplit lo ordenara (`commanded` no es `True`) o se ve encendida por primera vez, y termina cuando pasa a apagada sin que lo ordenara SolarSplit (`commanded` no es `False`). Un apagado de SolarSplit por falta de excedente no la termina. El motor recibe `on_demand` y `request`: sin petición la carga no es elegible (motivo `waiting_request`), nunca se enciende aunque haya excedente y sea la primera, y su consumo no se reasigna; si SolarSplit la dejó encendida sin petición, la apaga. Con petición se gestiona por prioridad como cualquier carga. Sustituye a «Respetar cambios manuales» (no se marca manual) y no marca `switched_off`: el apagado externo ya termina la petición. En climate, mientras dura la petición y está encendida se guardan `hvac_mode`, temperatura objetivo (`temperature` o `target_temp_low/high`) y `fan_mode` (`request_settings`); al volver a encenderla SolarSplit restaura esos valores en lugar del modo de la carga. En modo sombra las peticiones se siguen igual y solo se publica la decisión. Entidad `binary_sensor` «Petición activa» por carga bajo demanda.
- **Volver a gestionar automáticamente (0.2.0).** Al apagar Gestionar en una carga con esta opción, el coordinador calcula la próxima vez local de la hora configurada (`rearm_at`, en la memoria). Gestionar se vuelve a encender sola cuando la carga pasa de encendida a apagada o al llegar esa hora, adoptando el estado actual como al reactivarla a mano. Con Gestionar apagado la carga no es controlada: su consumo no se reasigna y cuenta como consumo de la casa.
- **Temporada (0.2.0).** Ajuste global opcional «Entidad de temporada» (`input_select` o `select`) y, por carga, «Temporadas» (multiselección con las opciones de esa entidad; vacío = todo el año; si no hay entidad, el campo no se muestra y se conservan las guardadas). El motor recibe `in_season` (`None` si la entidad no se puede leer: la carga se deja como esté, motivo `season_unknown`). Fuera de temporada la carga no es elegible (`out_of_season`) y, si la encendió SolarSplit (`owned`), se apaga sin retardo de apagado y respetando el tiempo mínimo encendida, como el apagado global; una encendida por otro se deja y su consumo no se reasigna. Una regulable recibe su límite mínimo. Se combina con la condición de uso: deben cumplirse las dos (la temporada se mira antes). Orden en el motor: Gestionar, disponible, apagada desde fuera, externa, manual, apagado global, condición global, temporada, condición de uso, petición.
- **Potencia aprendida por circuito (0.2.0).** El IoTaWatt mide circuitos, no aparatos (todos los aires en un canal, los radiadores por planta), así que no sirve como sensor de potencia de una carga. Campo opcional por carga «Sensor del circuito», distinto del sensor de potencia propio. La lógica está en `learning.py`, sin Home Assistant (`CircuitLearner`): el coordinador lee cada circuito una vez por ciclo y le pasa la lectura; cuando ve una transición de una carga con circuito (la haga SolarSplit o una persona) le pasa el cambio con la hora real (`last_changed`). Pasados 60 s calcula el salto: media del circuito en los 20 s anteriores frente a la media de 20 a 60 s después (se salta el arranque del compresor), con el signo del cambio. Se descarta si otra carga del mismo circuito cambió en esa ventana, si no hay lecturas en alguna de las dos, o si el salto es menor de 50 W o mayor que el doble de la potencia nominal. Las 10 últimas medidas válidas y la hora de la última van en la memoria persistente (`learned`, `learned_at`). Con 3 o más, su media sustituye a la nominal en el motor (`nominal_w`: para encenderla y como consumo mientras está encendida); si la carga tiene sensor de potencia propio, este manda siempre. En climate, mientras está encendida, el consumo que cuenta es el máximo entre esa potencia (aprendida o nominal) y la lectura del circuito repartida entre las cargas encendidas del circuito, para no sobreestimar el excedente. Sensor por carga con circuito «Potencia aprendida» (W): media de las medidas, con `measurements`, `last_measurement` e `in_use`.
- **Pendiente:** sondeo en inyección cero (el modo se detecta y el motor queda en pausa), balance neto horario, servicios, condiciones por plantilla y coordinación con TankWise.

## Casos especiales

**Termo y x10derivador (0.2.0).** FreeDS ya no existe. El termo lo regula el x10derivador, con firmware ESPHome propio, que apunta a un objetivo de red (`number.coladuria_x10_derivador_objetivo_de_red`, −50 W: regula para exportar unos 50 W). El termo se declara como **carga regulable**:

| Campo | Valor |
| --- | --- |
| Entidad | `switch.termo` (Shelly Pro 1PM delante del derivador) |
| Sensor de potencia | `sensor.termo_power` (medida real del Shelly; no la estimación del derivador) |
| Entidad de límite | `number.coladuria_x10_derivador_potencia_maxima` (0–3500 W, paso 50) |
| Entidad de modo / valor automático | `select.coladuria_x10_derivador_modo` / `Auto` (opciones `Apagado`, `Auto`, `Manual`) |
| Límite mínimo / máximo | 0 / 1600 W |

- En `Auto` su consumo es excedente y SolarSplit le escribe la potencia máxima según su prioridad: el máximo si nada va por delante, o lo que dejan las cargas anteriores.
- En `Manual` o `Apagado` su consumo es de red: carga «externa», no se reasigna.
- `switch.coladuria_x10_derivador_modo_manual`, el objetivo de red y `sensor.coladuria_x10_derivador_consigna` son del derivador; SolarSplit no los usa.

**Seguridad del termo.** El corte del Shelly es independiente de SolarSplit y no debe implementarse en ella: ni temperaturas, ni tiempos máximos, ni rearmes. SolarSplit nunca conmuta `switch.termo`; si está apagado, la carga queda bloqueada (`switched_off`). Lo único que puede hacer es bajar la potencia máxima del derivador, y la devuelve al dejar de gestionar el termo.

**Coordinación con TankWise:** pendiente, no implementada.

Las dos automatizaciones de calentar en inyección cero si baja de 36 °C no son reparto de excedente sino una regla de confort. Se quedan como automatizaciones.

**Aires.** Hoy P0 enciende tres equipos a la vez. En la integración cada aire es una carga independiente (Sara, Juande Jr, Annabel, Salón) con su potencia, de modo que entran de uno en uno según haya presupuesto. Tiempo mínimo encendido sugerido: 15 min, para no castigar el compresor. Condición de uso: `input_boolean.climatizar_casa`. Acción: `climate.set_hvac_mode` con `cool` u `off`; en invierno se podría usar `heat` con un selector de modo por temporada.

**Depuradora.** Objetivo diario (0.2.0): sensor de horas `sensor.piscina_on_hoy` y entidad de horas objetivo; las horas por temporada (`input_number.horas_invierno_piscina`, `input_number.horas_verano_piscina`, según `input_select.temporada_piscina`) se dan a SolarSplit en una sola entidad (por ejemplo un ayudante de plantilla). Hora de inicio opcional: pendiente. `binary_sensor.auth_piscina` deja de hacer falta: lo sustituye el presupuesto. La automatización de cambio de temporada se queda.

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
