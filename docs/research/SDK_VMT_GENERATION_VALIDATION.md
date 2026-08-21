# SDK 2013 SP: staged VMT generation validation

Дата проверки: 2026-08-21.

## Цель

Проверить на реальном Crowbar/StudioMDL новый staged `generate -> validate` coordinator и compile-совместимость трёх material policies:

- direct VMT без color-key -> generated Patch `insert`;
- direct VMT с существующим `$color2` -> generated Patch `replace`;
- исходный Patch -> раскрытая full-copy VMT с заменённым `$color2`.

Проверка выполняется opt-in тестом `tests/test_external_sdk_generation.py` при `PSR_RUN_EXTERNAL_SDK=1`.

## Изоляция

- Antenna `GameInfo.txt`, исходный MDL/VMT и SDK binaries используются только для чтения.
- Overlay VMT для `replace` и source-Patch cases создаются во временном каталоге раньше Antenna в ordered SearchPaths.
- Crowbar, generated VMT, transformed QC и StudioMDL outputs находятся только в marker-protected `StagingWorkspace` с отдельным minimal `GameInfo.txt`.
- После каждого case staging автоматически удаляется. `models/psr_scaled` и `materials/models/psr_scaled` реального проекта не создаются и не изменяются.

Проверены зафиксированные binaries:

| Инструмент | SHA-256 |
|---|---|
| CrowbarCommandLineDecomp.exe 0.68 | `4B5FC8F5092448C1F8FE12F6849BF8EE3996406F02109EC90AB800C6CF145B2A` |
| StudioMDL SDK 2013 SP | `E6C4EA7477B8CE31DE878FF53CA640CB222C4978F3BA33C4715DE3DE1C7A6416` |

## Реальная модель

Все cases используют read-only `models/props_se/storage/book_2.mdl`, source skin 0, RGB `190 48 148` и compile scale `1.50`. Модель dynamic, имеет одну кость и PHY, поэтому generated identity — `book_2_scaled_150.mdl`, а QC geometry scale — `2.25`.

Первая попытка обнаружила реальный pre-compile defect в MDL/QC contract: MDL adapter сохранял только mesh-used skin slot и возвращал таблицу 8×1, тогда как Crowbar корректно восстанавливал полную QC-таблицу 8×8. Coordinator остановился с `qc_source_skinfamilies_mismatch` до StudioMDL и публикации.

Исправленный adapter теперь разделяет:

- полную numeric MDL skin-reference table для точного QC round-trip;
- отсортированные mesh-used material slots для VMT resolution и покраски.

Для `book_2` полная таблица имеет восемь rows по восемь slots, но mesh использует только slot 0. Поэтому одна цветная skin family сохраняет семь unused исходных значений и создаёт ровно один colored VMT для видимого материала.

## Матрица

| Case | Source | Generated mode | Assignment | StudioMDL | Companions |
|---|---|---|---|---|---|
| real direct VMT | Antenna folder VMT без color-key | Patch | `insert $color2` | success | 6/6 |
| overlay direct VMT | temporary VMT с `$color2` | Patch | `replace $color2` | success | 6/6 |
| overlay source Patch | temporary Patch + base VMT | full-copy `VertexLitGeneric` | effective `$color2` replacement | success | 6/6 |

Во всех cases:

- Crowbar декомпилировал один однозначный QC;
- reference QC точно совпал с полной исходной MDL skin table до добавления generated row;
- StudioMDL завершился с exit code 0 без `KeyValues Error`;
- generated MDL получил точный managed internal name и static-prop flag;
- валидированы непустые `.mdl`, `.vvd`, `.dx80.vtx`, `.dx90.vtx`, `.sw.vtx` и `.phy`.

## Граница результата

Матрица подтверждает generation policy, структурную корректность VMT, Patch/full-copy semantic round-trip в `srctools` и compile-совместимость со StudioMDL SDK 2013 SP. Она не доказывает фактический оттенок пикселей в игре: визуальная runtime-проверка `$color`/`$color2`, Patch `insert`/`replace` и proxies остаётся отдельным screenshot/viewport regression уровнем.

До такой проверки production policy остаётся консервативной: direct VMT использует generated Patch, а исходный Patch — full-copy fallback.
