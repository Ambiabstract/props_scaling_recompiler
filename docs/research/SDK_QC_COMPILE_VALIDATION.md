# SDK 2013 SP: staged QC compile validation

Дата проверки: 2026-08-21.

## Цель

Проверить на реальном toolchain, что утверждённый QC transformer компилирует managed static-модели без специальных числовых переписываний collision, explicit bounds и bones. Проверка отделяет линейную compile identity (`_scaled_150`) от model-aware geometry scale (`1.50` или `2.25`).

## Изоляция

- Crowbar читает исходные MDL Antenna, но пишет decompile только в уникальный `StagingWorkspace` внутри репозитория.
- StudioMDL получает отдельный staging game root и пишет только под его `models/psr_scaled/sdk_matrix/`.
- Исходные MDL/QC/SMD, Antenna `GameInfo.txt`, managed roots реального проекта и SDK не изменяются.
- После фиксации результатов все staged decompile/compile files удалены marker-проверенным cleanup.

Toolchain соответствует зафиксированному окружению проекта:

| Инструмент | Версия | SHA-256 |
|---|---:|---|
| `CrowbarCommandLineDecomp.exe` | 0.68.0.0 | `4B5FC8F5092448C1F8FE12F6849BF8EE3996406F02109EC90AB800C6CF145B2A` |
| `studiomdl.exe` | SDK 2013 SP | `E6C4EA7477B8CE31DE878FF53CA640CB222C4978F3BA33C4715DE3DE1C7A6416` |

## Реальная матрица

Во всех cases compile scale равен `1.50`; generated identity заканчивается на `_scaled_150.mdl`.

| Source model/QC | Source state | Collision | Исходный `$scale` | Geometry `$scale` | Reference mutation | Результат |
|---|---|---|---:|---:|---|---|
| `models/apt/fsmit01.mdl` | static, 1 bone | `$collisionmodel` | отсутствует | `1.50` | нет | success |
| `models/apt/monitor01.mdl` | dynamic, 1 bone | `$collisionmodel` | отсутствует | `2.25` | `insert_staticprop` | success |
| `models/props_se/doll01.mdl` | dynamic, 1 bone | `$collisionmodel` | отсутствует | `2.25` | `insert_staticprop` | success |
| `modelsrc/props_vehicles/car_van1a_doors1a.qc` + соответствующий MDL | dynamic, 1 bone | `$collisionjoints` | `1` | `2.25` | `insert_staticprop` | success |

В последнем случае variant mutation именно заменяет существующий `$scale`, а не умножает его: `replace_modelname`, `replace_scale`.

Для каждого case подтверждены:

- StudioMDL exit code `0`;
- точный managed internal model name в MDL header;
- MDL version в поддерживаемом диапазоне и установленный static-prop flag;
- непустые `.mdl`, `.vvd`, `.dx80.vtx`, `.dx90.vtx`, `.sw.vtx` и `.phy`;
- сохранение исходных collision blocks, `$bbox`, `$cbox` и `$definebone` без специальных правок.

## Synthetic contract matrix

Автоматический `tests/test_staging_toolchain.py` дополняет реальные cases полным перекрёстным набором из 12 комбинаций:

```text
source state:  static | dynamic
collision:     none | $collisionmodel | $collisionjoints
source scale:  absent | present
```

Для каждой комбинации проверяются итоговый `$staticprop`, замена/вставка `$scale`, сохранение collision и `$bbox`, масштабирование top-level `$lod` и отдельный content hash staged QC.

## Граница результата

Матрица доказывает compile compatibility выбранной стратегии и структуру выходного artifact set. Она не заменяет viewport/BSP regression геометрических размеров render mesh и collision для широкой выборки моделей. Такой regression остаётся отдельным уровнем проверки перед end-to-end commit в реальный проект.
