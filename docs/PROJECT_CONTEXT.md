# Память проекта props_scaling_recompiler

Дата фиксации: 2026-08-21.

## Назначение

`props_scaling_recompiler` (PSR) — compile-time инструмент для Hammer++, который заменяет `prop_static_scalable` на обычный `prop_static` и автоматически создаёт необходимые варианты Source-моделей. Основные задачи 2.0:

- равномерное масштабирование модели вместе с геометрией, LOD и collision;
- преобразование подходящей dynamic/physics-модели в static-вариант;
- покраска static prop через дополнительные материалы и skin families;
- повторное использование готовых артефактов между картами одного проекта;
- безопасная актуализация generated-контента.

Инструмент запускается отдельным этапом Hammer compile-run. Он получает `-game`, `-vmf_in` и `-vmf_out`, подготавливает ассеты и создаёт VMF для последующих стадий компиляции карты.

## Текущее состояние репозитория

- Основная ветка исследования: `psr_200_dev_002`.
- `props_scaling_recompiler_v1.1.2.py` — последняя релизная реализация и текущий поведенческий baseline.
- `props_scaling_recompiler.py` — незавершённый архитектурный прототип 2.0.
- Прототип 2.0 синтаксически корректен, но функционально не завершает pipeline: содержит интерактивные остановки, заглушки генерации QC/компиляции, не сохраняет новый кэш и не пишет `vmf_out`.
- Создан начальный проектный фундамент: `pyproject.toml`, пакет `psr` с архитектурными границами и автономные smoke/contract-тесты, совместимые с pytest и стандартным `unittest`.
- Зафиксированы synthetic fixtures для VMF, GameInfo и VMT/Patch, а также test-only scale oracle из 51 сущности `psr_scale_compatibility_01a.vmf`: 35 parsing cases и model-dependent geometry matrix.
- Добавлен первый `srctools` integration spike: ordered SearchPaths plan, ручная цепочка folder/VPK, точный logical-path lookup и provenance победившего источника. Synthetic VPK создаются только во временных каталогах тестов.
- Добавлен production-адаптер `psr.assets.mdl`: он разрешает исходный MDL через ordered SearchPaths, читает MDL 44–49 посредством `srctools.mdl.Model` и возвращает immutable `SourceAssetMetadata` с provenance, SHA-256 MDL/companions, static flag, bone count, `$cdmaterials`, skin families и точными ссылками на найденные VMT. `models/psr_scaled/**` отклоняется до чтения как managed output.
- Добавлены детерминированные synthetic MDL v44/v48 fixtures и contract-тесты для folder/VPK, static/dynamic, нескольких материалов и skins, отсутствующего VMT и повреждённого offset. Бинарники fixtures строятся во временном каталоге и не коммитятся.
- Добавлен байтовый source-preserving VMF parser `psr.keyvalues.vmf`: он сохраняет исходные bytes и spans, порядок и повторяющиеся свойства/блоки, различает direct и nested properties, понимает комментарии и не выполняет сериализацию при discovery.
- Добавлен read-only pipeline `discover_vmf_requests -> inspect_map_sources -> build_operation_plan`. Он игнорирует entities внутри top-level `hidden`, связывает активные VMF requests с `SourceAssetMetadata`, агрегирует generated-model requirements независимо от color/skin и color requirements независимо от scale, но ничего не генерирует и не изменяет VMF.
- Добавлен pure resolver `psr.domain.resolve_compile_scale`, совпадающий со всеми 35 подтверждёнными Hammer++ cases. Он читает беззнаковый десятичный prefix, использует fallback `1.0`, применяет PSR clamp `0.01` и decimal `ROUND_HALF_UP` до сотых; prefix/fallback/clamp/rounding становятся явными warnings `OperationPlan`. `scaled_model_path()` детерминированно назначает managed `_scaled_XXX` path из целого процента, форматируя его с минимальной шириной 3.
- Добавлен model-aware `psr.domain.resolve_geometry_scale`: для one-bone non-static MDL он возвращает `compile_scale²`, для static или многокостной модели — линейный compile scale. Geometry ниже `0.01` получает отдельный product clamp и диагностику. Compile identity и имя `_scaled_XXX` остаются линейными.
- Добавлен production-адаптер `psr.assets.vmt` поверх `srctools.vmt.Material`: он разрешает исходный VMT через ordered SearchPaths, раскрывает Patch, нормализует shader/parameters/blocks/proxies, сохраняет provenance и SHA-256 каждого VMT в dependency graph и вычисляет единый dependency fingerprint. Managed input `materials/models/psr_scaled/**` отклоняется до чтения.
- Добавлена отдельная read-only material phase `inspect_colored_material_sources -> build_colored_material_plan`. Она дедуплицирует identity по полному source VMT path и canonical RGB, назначает managed VMT paths, выбирает существующий `$color`/`$color2` либо `$color2` для подтверждённых `VertexLitGeneric`/`UnlitGeneric`, а неподтверждённые shader'ы превращает в явную ошибку. Прямой VMT планируется как generated Patch; исходный Patch консервативно планируется как `full_copy` до SDK-проверки Patch-chain. Итоговый skin index намеренно остаётся следующему cache-backed skin-layout этапу.
- Добавлена synthetic VMT matrix для отсутствующего/existing color-key, proxies, Patch, unsupported shader и folder/VPK provenance. Material phase ничего не записывает и не запускает Crowbar/studiomdl.
- Добавлен project-scoped JSON manifest schema v1 в `psr.cache`: нормализованный абсолютный `GameInfo.txt` задаёт стабильный `project_id`, текущий SHA-256 GameInfo хранится отдельно, а SourceAsset/GeneratedModel/ColoredMaterial/SkinMapping/MapUsage являются раздельными строго валидируемыми таблицами. Поддержаны migration v0→v1, безопасный cold start/recovery повреждённого или несовместимого cache и атомарная запись temp+`os.replace`; путь хранения пока передаётся явно до CLI-интеграции.
- Добавлен pure cache-backed `build_skin_layout_plan`: исходные skin indices сохраняются, валидные ранее назначенные colored mappings удерживают индексы, новые `(source skin, RGB)` сортируются и дописываются после них. Полный layout получает fingerprint; entity получает отдельный final skin assignment. `commit_skin_layout_plan` только строит manifest-кандидат и не пишет его: вызывать его разрешено после будущей проверки generated artifacts. При изменении исходной skin table старые mappings и связанные `MapUsage` других карт для модели инвалидируются.
- Добавлен собственный байтовый `psr.assets.qc`: token-aware lexer валидирует quotes/comments/braces, различает top-level и вложенные команды и применяет только span-edits, не пересериализуя остальной QC. Reference transform сверяет исходный `$texturegroup "skinfamilies"` с таблицей MDL, добавляет `$staticprop` для dynamic source и устанавливает полный стабильный layout. Variant transform назначает managed `$modelname`, записывает отдельный geometry scale в `$scale` и умножает на него только top-level `$lod` distances; compile identity остаётся в имени и плане, collision/bounds и остальные команды остаются побайтно нетронутыми до compile-validation.
- Добавлен pure `build_qc_operation_plan`: один reference QC переиспользуется всеми scale variants исходной модели; каждый in-memory artifact имеет source/output SHA-256, список mutations и детерминированный staging-relative path. Отсутствующий QC, рассогласование static flag MDL/QC и stale skin table становятся diagnostics. Этап ничего не пишет и не запускает Crowbar/studiomdl.
- Добавлен изолированный `StagingWorkspace`: каждый operation получает уникальный marked root под явно заданным parent, пути не могут выйти за его границы, повторная запись с другим content запрещена, source MDL/companions перед materialization повторно сверяются по provenance, size и SHA-256. Cleanup удаляет только собственный marker-protected root; staging можно явно сохранить для диагностики.
- Добавлены argv-only adapters Crowbar и StudioMDL без `shell=True`, с timeout, byte-preserving stdout/stderr и категоризированными ошибками запуска/exit code. Crowbar считается успешным только при одном однозначном QC в предварительно пустом output directory. StudioMDL отделён от post-compile validation: generated model проверяется по managed path, непустому полному companion set, MDL version/internal name и static-prop flag, а не по наличию строки `Completed` в логе.
- Добавлен детерминированный generator цветных VMT `psr.assets.generate_colored_material`: planned direct VMT становится generated Patch, planned source Patch — раскрытой full-copy с effective shader/parameters/blocks/proxies; выбранный `$color`/`$color2` получает канонический integer RGB vector. Каждый output повторно парсится через `srctools.vmt.Material` и получает SHA-256. Это подтверждает структуру и semantic round-trip generated текста, но не заменяет отдельную runtime-проверку Patch `insert`/`replace` на SDK 2013 SP.
- Добавлен staged coordinator `psr.pipeline.generate_and_validate`. Он принимает только валидные operation/material/skin plans, повторно проверяет VMT dependency graph и source MDL provenance/hash, генерирует VMT в staging game root, декомпилирует каждый уникальный source model один раз, строит reference/variant QC, размещает compile-ready variant рядом с Crowbar QC для сохранения относительных путей, запускает StudioMDL и валидирует каждый обязательный companion. Возвращаемый immutable `GenerationResult` ничего не публикует в реальный проект, manifest или VMF.
- Добавлен synthetic subprocess end-to-end contract: два scale variants и два цветных материала проходят через одну fake-Crowbar декомпиляцию и две fake-StudioMDL компиляции; отдельный dynamic scale 1.0 получает static `_scaled_100`; no-op не запускает tools; изменение VMT после planning и отсутствие `.sw.vtx` останавливают операцию, после чего marker-protected staging удаляется, а внешний sentinel остаётся неизменным.
- Synthetic compile-validation matrix из 12 комбинаций перекрёстно покрывает static/dynamic source, отсутствие collision/`$collisionmodel`/`$collisionjoints` и отсутствие/наличие исходного `$scale`. Она подтверждает, что transformer добавляет `$staticprop` только при необходимости, заменяет исходный `$scale` итоговым geometry scale, сохраняет collision и explicit bounds побайтно и масштабирует только top-level LOD distance.
- Реальная isolated SDK 2013 SP matrix успешно декомпилировала Crowbar 0.68 и скомпилировала StudioMDL четыре staged cases при compile scale 1.50: static `apt/fsmit01` с `$collisionmodel` и geometry 1.50; one-bone dynamic `apt/monitor01` и `props_se/doll01` с `$collisionmodel` и geometry 2.25; one-bone dynamic `props_vehicles/car_van1a_doors1a` с `$collisionjoints`, исходным `$scale 1` и geometry 2.25. Во всех случаях exit code равен 0, generated MDL имеет точный managed internal name и static flag, выпущены непустые `.mdl`, `.vvd`, `.dx80.vtx`, `.dx90.vtx`, `.sw.vtx` и `.phy`. Внешние Antenna/SDK assets не изменялись; компиляция выполнялась в отдельный staging game root. Протокол и границы результата зафиксированы в `docs/research/SDK_QC_COMPILE_VALIDATION.md`.
- Исследовательские скрипты `is_staticprop.py`, `skins_from_mdl.py` и `mdl_skins_and_cdmaterials*.py` подтверждают возможность чтения static flag, material table, `$cdmaterials` и skin families непосредственно из MDL.
- Пользовательское незакоммиченное изменение в `props_scaling_recompiler.py`: версия `2.0.0 - dev 001` заменена на `2.0.0 - dev 002`.

## Целевая среда 2.0

Первый релиз PSR 2.0 поддерживает только:

- Windows;
- Source SDK 2013 Singleplayer;
- Hammer++;
- MDL и toolchain фактической SDK 2013 SP среды пользователя.

Garry's Mod, Portal 2 и другие ветки Source не входят в scope первого релиза 2.0.

Локальное референсное окружение пользователя:

- SDK bin: `C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin`;
- реальный мод: `C:\Program Files (x86)\Steam\steamapps\sourcemods\antenna_sdk2013`;
- production-комплект 1.1.2: `prod/props_scaling_recompiler_v1.1.2`.

Зафиксированные внешние инструменты:

| Инструмент | Версия | SHA-256 |
|---|---:|---|
| CrowbarCommandLineDecomp.exe | 0.68.0.0 | `4B5FC8F5092448C1F8FE12F6849BF8EE3996406F02109EC90AB800C6CF145B2A` |
| vpkeditcli.exe | 4.2.3 | `A28E5B596161995BEE529DF2FCB06F754482255B2306F697D4FEF4F6F79BEA2A` |
| studiomdl.exe из SDK 2013 SP | file version отсутствует | `E6C4EA7477B8CE31DE878FF53CA640CB222C4978F3BA33C4715DE3DE1C7A6416` |

Бинарники Crowbar и VPKEdit из production-комплекта побайтно совпадают с установленными в SDK `bin`.

## Роль srctools 2.7.0

`srctools==2.7.0` принят как закреплённая runtime-зависимость PSR 2.0. Библиотека распространяется под MIT и предоставляет проверенные реализации для Valve KeyValues, виртуальных файловых систем, VPK, MDL и VMT. В проекте она используется через собственные тонкие adapters, чтобы продуктовые правила PSR не зависели от неявной политики высокоуровневых helpers.

Upstream и документация: `https://github.com/TeamSpen210/srctools`, `https://srctools.readthedocs.io/`.

Подтверждённые области повторного использования:

- `srctools.keyvalues` — semantic parse и структурная валидация GameInfo/VMT, а также дополнительная проверка VMF;
- `srctools.filesys.RawFileSystem`, `VPKFileSystem` и `FileSystemChain` — точный lookup полного logical path в папках и directory VPK;
- `srctools.mdl.Model` — MDL versions 44–49, static-prop flag, `$cdmaterials`, skin families и разрешение материалов;
- `srctools.vmt.Material` — shader/parameters/proxies, раскрытие Patch и сбор его include-зависимостей;
- встроенный PyInstaller hook — для будущей Windows-сборки.

Ограничения интеграции:

- `srctools.vmf.VMF.export()` не является source-preserving writer: он нормализует форматирование, удаляет обычные комментарии, меняет порядок/представление части данных, схлопывает повторяющиеся direct entity keys и по умолчанию увеличивает map version. Итоговый VMF поэтому редактируется собственным lossless span-editor; `srctools.vmf` допустим как semantic reader/validator.
- `srctools.game.Game.get_filesystem()` нельзя использовать как resolver PSR. В версии 2.7.0 он группирует VPK раньше folder roots и автоматически добавляет некоторые DLC/update/platform paths, поэтому фактический порядок отличается от строк `SearchPaths`. PSR самостоятельно разбирает GameInfo и вручную собирает `FileSystemChain` строго в утверждённом порядке.
- `srctools.mdl.Model` 2.7.0 отбрасывает неиспользуемые material slots, проходя по Python `set`. Чтобы этот неустойчивый порядок не стал частью skin-layout identity, адаптер PSR повторно читает только offsets таблиц texture/skin/bodypart/model/mesh, сортирует числовые material-slot indexes и сверяет результат с `srctools` без учёта порядка внутри family.
- QC библиотекой не поддерживается; для него остаётся собственный token-aware transformer.
- Семантика VMT Patch из библиотеки полезна как parser/evaluator, но реальный выбор `$color`/`$color2` и поведение `insert`/`replace` всё равно проверяются на SDK 2013 SP.

Read-only probe на Antenna подтвердил применимость установленной версии и собственных ordered/MDL adapters: 35 исходных SearchPath leaves развёрнуты в 39 конкретных mounts без группировки VPK перед folder roots; `models/props_se/storage/book_2.mdl` разрешён через исходную строку `|gameinfo_path|.` из project folder. Production-адаптер определил MDL v48 как non-static, сохранил восемь skin families, разрешил первые четыре реально существующих VMT по `models/props_se/book/` и зафиксировал MDL, PHY, VVD и три VTX companions с размерами и SHA-256. Отсутствующие optional paths, numbered VPK chunks и отсутствующие VMT остаются явным состоянием/diagnostics, а не ломают discovery.

Read-only QC inventory разобрал без structural errors все 392 найденных `.qc` в Antenna и сохранённом SDK temp: 355 static и 37 dynamic scripts, 147 со `$scale`, 7 scripts/15 commands с `$lod`, 257 с `$collisionmodel`, 10 с `$collisionjoints` и 8 со `skinfamilies`. Реальный Crowbar 0.68 QC `fsmit01.qc` подтвердил важную QC-лексему `$cdmaterials "models\apt\"`: обратный слеш перед закрывающей кавычкой является path separator, а не KeyValues-style escape. Его skin row точно совпал с MDL `models/apt/fsmit01.mdl`; reference transform при неизменном layout вернул исходные bytes без mutations. Внешние файлы не изменялись, Crowbar и studiomdl не запускались.

`vpkeditcli.exe` пока остаётся в зафиксированном toolchain как baseline/fallback. Возможность исключить его из поставки рассматривается только после regression-проверки `srctools` на реальных VPK Antenna.

## Утверждённые продуктовые правила

### Оригиналы

Оригинальные MDL, QC, VMT и companion-файлы никогда не изменяются. Все преобразования создают новые артефакты.

Оригинальная модель используется напрямую только при одновременном выполнении условий:

- она уже имеет static-prop flag;
- итоговый PSR compile scale равен 1.0;
- запрошенный цвет равен `255 255 255`.

Любая другая комбинация создаёт managed-модель PSR, включая dynamic при scale 1.0 и покрашенный static при scale 1.0.

### Модели

Корень generated-моделей:

```text
models/psr_scaled/
```

Внутренняя структура исходного пути сохраняется. Пример:

```text
models/props_lab/cactus.mdl
-> models/psr_scaled/props_lab/cactus_scaled_150.mdl
```

Единственный суффикс — `_scaled_XXX`. Старый `_static` больше не создаётся и не интерпретируется:

```text
models/example/foo_static.mdl
-> models/psr_scaled/example/foo_static_scaled_100.mdl
-> models/psr_scaled/example/foo_static_scaled_150.mdl
```

Источник истины для Hammer-compatible effective scale — видимое поведение Hammer++. Сначала PSR нормализует raw `modelscale` в линейную compile identity: значение ниже `0.01` клампится до `0.01`, затем округляется через decimal `ROUND_HALF_UP` до сотых. Clamp защищает от практически бесполезных и дорогих сверхмалых моделей, а округление обеспечивает однозначный целочисленный процент `_scaled_XXX`. Затем из compile identity и MDL metadata выводится фактический geometry scale: квадрат для one-bone non-static source, линейное значение для остальных; после квадрата также применяется product clamp `0.01`. Все clamp/rounding преобразования явно диагностируются. Подтверждённые parsing-примеры:

```text
blablabla -> 1.0
1,0        -> 1.0
3,0        -> 3.0
0.001      -> Hammer effective 0.001 -> PSR compile 0.01
1.095      -> PSR compile 1.10 -> _scaled_110
1.104      -> PSR compile 1.10 -> _scaled_110
1.105      -> PSR compile 1.11 -> _scaled_111
```

В production-модели данных и кэше хранятся raw-строка `modelscale` и итоговый PSR compile scale после Hammer-совместимой нормализации, нижнего clamp и округления. Именование, generated identity и кэш опираются на compile scale; raw сохраняется в `MapUsage` для provenance и диагностики. Geometry scale детерминированно выводится из compile scale, MDL bone count и static flag, присутствует в operation/QC plan, но не становится независимой persistent identity. Hammer-compatible effective scale является тестовым oracle и не входит в cache schema. Разные raw-значения намеренно схлопываются в один `_scaled_XXX`, если дают одинаковый округлённый compile scale. Числовая identity равна точному целому `compile_scale * 100`; в имени она форматируется с минимальной шириной 3 и ведущими нулями для коротких значений (`001`, `050`, `110`, но `1000` и `5500` не обрезаются). Внутренняя структура исходного model path сохраняется под `models/psr_scaled/`.

### Материалы

Корень generated-материалов:

```text
materials/models/psr_scaled/
```

Предпочтительно сохранять структуру логического пути исходного материала. Пример целевого вида:

```text
materials/models/props_lab/cactus_sheet.vmt
-> materials/models/psr_scaled/props_lab/cactus_sheet_col_114_191_102.vmt
```

Один generated-материал идентифицируется полным логическим путём исходного VMT и RGB. Он может переиспользоваться всеми масштабами и моделями, которые используют то же исходное сочетание.

## Модель данных

Прототипный `scary_key = model + scale + skin + color` смешивает разные сущности. Целевая модель должна разделять как минимум:

### SourceAsset

- логический Hammer-путь модели;
- фактический источник: папка или конкретный VPK;
- hashes/signatures MDL и необходимых companion-файлов;
- static flag;
- material names, `$cdmaterials`, skin families;
- версия прочитанного MDL-формата.

Текущий `SourceAssetMetadata` является результатом read-only discovery, а не преждевременно утверждённой cache schema. В persistent manifest попадут только поля, необходимые для identity/invalidation, после отдельного проектирования versioned schema и миграций.

### GeneratedModel

- ссылка на SourceAsset;
- итоговый PSR compile scale и его каноническое представление;
- выходной logical/physical path;
- static conversion state;
- fingerprint skin layout;
- список ожидаемых companion-файлов;
- статус генерации и проверки.

Identity модели не должна включать выбранный одной сущностью исходный skin или цвет. Одна модель конкретного масштаба содержит общую согласованную таблицу необходимых skin families.

### ColoredMaterial

- логический путь исходного VMT;
- RGB;
- выбранный color parameter (`$color` или `$color2`);
- режим генерации (`patch` или `full_copy`);
- выходной путь;
- hash/fingerprint исходного материала и зависимостей Patch.

### SkinMapping

- SourceAsset;
- исходный skin index;
- RGB;
- итоговый skin index;
- fingerprint layout, для которого mapping действителен.

Mapping должен быть стабильным между запусками. Новые комбинации добавляются в детерминированном порядке либо получают сохранённые индексы из валидного кэша.

### MapUsage

- стабильный идентификатор карты, не только basename;
- entity ID;
- исходный запрос модели/raw scale/skin/color и итоговый PSR compile scale;
- GeneratedModel и итоговый skin index;
- версия последнего успешного анализа карты.

### ProjectCache / Manifest

- кэш является общим для карт только внутри одного проекта и никогда не объединяет данные разных проектов;
- явная schema version;
- нормализованный project identity на основе GameInfo;
- SourceAsset, GeneratedModel, ColoredMaterial, SkinMapping и MapUsage как отдельные таблицы/коллекции;
- атомарная запись через временный файл и replace;
- возможность отклонить или мигрировать несовместимую схему;
- отсутствие зависимости от импортируемых Python dataclass через неустойчивый pickle-граф.

Граница проекта определяется нормализованной идентичностью его `GameInfo.txt`, а не именем карты, basename каталога или местом запуска PSR. Любые cache lookup, reuse артефактов, `MapUsage`, cleanup и блокировки выполняются только внутри этой project identity. Совпадающие logical paths, модели, материалы или имена карт в двух разных проектах не дают права переиспользовать между ними cache records или считать их одним managed-состоянием.

## Целевой pipeline

```text
validate inputs
  -> parse VMF structurally
  -> collect active PSR entity requests
  -> resolve GameInfo SearchPaths
  -> resolve and inspect source assets
  -> load/validate cache and filesystem state
  -> build deterministic generation plan
  -> stage/decompile source assets
  -> generate colored materials and reference QC
  -> generate QC for required scales
  -> compile into staging/output roots
  -> verify every expected model companion and material
  -> atomically update cache/manifest
  -> structurally write and validate vmf_out
  -> emit one categorized summary and exit code
```

Если активных PSR-сущностей нет, `vmf_out` всё равно должен быть создан как эквивалент `vmf_in`.

Реализованный pre-generation `OperationPlan` всегда устанавливает `requires_vmf_output=True`, включая no-op карту. Он хранит raw `modelscale` в `VmfEntityRequest`, получает итоговый `Decimal` compile scale, выводит отдельный model-dependent geometry scale и назначает детерминированный output model path. План определяет `reuse_original` только для static + compile scale 1.0 + white; остальные usages указывают на `models/psr_scaled/**/_scaled_XXX.mdl`. Model requirements агрегируются уже по округлённому compile scale, поэтому `1.095`, `1.1` и `1.104` переиспользуют одну `_scaled_110`; geometry остаётся однозначной благодаря source fingerprint с MDL metadata.

Нельзя записывать VMF, указывающий на неподтверждённый артефакт. Частично успешная генерация должна либо сохранить корректные ранее существовавшие артефакты, либо завершиться до commit-этапа с понятным отчётом.

## GameInfo и разрешение ассетов

Реальный Antenna `GameInfo.txt` содержит:

- `|gameinfo_path|` и `|all_source_engine_paths|`;
- `.` и wildcard `*`;
- явные `_dir.vpk` и имена VPK без `_dir`;
- относительные `ep2/...` и `lostcoast/...` пути;
- разные search path modes;
- отсутствующие и закомментированные опциональные пути.

Требования resolver:

- сохранять исходный порядок SearchPaths;
- искать точный полный logical path;
- первый успешный источник побеждает;
- после успеха не продолжать поиск по другим VPK;
- не считать совпадение basename доказательством наличия файла;
- отдельно индексировать folder roots и directory VPK;
- не передавать numbered VPK chunks как самостоятельные архивы;
- сохранять provenance найденного файла для диагностики и cache invalidation;
- отличать нормальный отсутствующий optional path от ошибки разрешения реально запрошенного ассета.

Свежий WIP-лог показал конкретную ошибку: один material path извлекался сначала из project VPK, затем из engine VPK, потому что поиск продолжался после первого совпадения.

## VMF и KeyValues

Релиз 1.1.2 читает и изменяет VMF регулярными выражениями, зависящими от порядка ключей. Это нельзя переносить в 2.0.

VMF reader/writer должен:

- поддерживать вложенные блоки `solid`, `side`, `connections`, `editor`, `hidden` и другие;
- различать direct properties блока и свойства вложенных блоков;
- сохранять повторяющиеся ключи и блоки;
- сохранять комментарии, порядок, newline style, encoding и нетронутые байты;
- игнорировать hidden PSR entities как запросы, не повреждая их;
- изменять только целевую entity по direct `id`/`classname`;
- после записи повторно парсить результат и доказывать ожидаемое число замен.

## Покраска

Утверждённое направление:

1. Найти фактический исходный VMT по ordered SearchPaths.
2. Определить shader, существующие `$color`/`$color2`, Patch-зависимости и proxies.
3. Предпочесть маленький generated Patch, включающий оригинальный VMT.
4. Выбрать корректную операцию добавления/замены color-key после тестов на реальном SDK.
5. Если Patch ненадёжен для конкретного случая, создать полноценную VMT-копию.
6. Добавить generated materials в согласованную skin-family table reference QC.
7. Скомпилировать все необходимые масштабы с одинаковым детерминированным layout.
8. Записать в VMF итоговый skin index вместо исходной пары skin/color.

Обязательная test matrix:

- VertexLitGeneric и другие реально встречающиеся model shaders;
- `$color`/`$color2` отсутствует;
- color-key уже существует;
- оригинальный VMT сам является Patch;
- proxies;
- VMT из folder SearchPath и VPK;
- один VMT используется несколькими моделями;
- исходные skin families меняются после заполнения кэша;
- несколько новых цветов запрашиваются в разном порядке на разных картах.

## QC generation

- Декомпилированный QC рассматривается как staged input: оригинальный или внешний QC никогда не перезаписывается.
- QC не является KeyValues. Parser учитывает line/block comments, quoted values, brace depth и top-level command boundaries; нетронутые bytes, encoding и newline style сохраняются.
- До изменения `skinfamilies` исходные rows QC сравниваются со всеми rows MDL. Несовпадение блокирует generation, чтобы stale decompile не сдвинул skin indices.
- Один reference QC содержит `$staticprop` и полный cache-backed skin layout; он сам не компилируется и служит источником для всех требуемых scale variants.
- Каждый variant получает `$modelname` без префикса `models/`, но строго под `psr_scaled/`; имя использует compile identity, а `$scale` — model-dependent geometry scale, не произведение с прежним `$scale` из decompile.
- Top-level `$lod` distance умножается на geometry scale. `$shadowlod`, collision blocks, explicit bounds, bones, sequences и прочие команды не изменяются неявными числовыми regex-заменами. Staged SDK matrix подтвердила успешную компиляцию сохранённых `$collisionmodel`, `$collisionjoints`, `$bbox`, `$cbox` и `$definebone` в выбранных static/dynamic cases; это подтверждение compile compatibility, а не отдельное доказательство viewport geometry для каждой возможной модели.
- Квадратичный geometry scale утверждён только для исходного MDL с одной костью без static-prop flag; прежняя эвристика по `prop_data` отвергнута. Staged matrix подтвердила, что StudioMDL принимает такой `$scale` вместе с сохранёнными render/collision-командами; точная viewport geometry для более широкой модельной выборки остаётся regression-задачей. Массовое комментирование `$bbox`/`$definebone` не считается утверждённым поведением 2.0.

## Cleanup и миграция

Нормальный cleanup 2.0 работает только внутри:

```text
models/psr_scaled/
materials/models/psr_scaled/
```

Эти каталоги считаются managed-пространством PSR. Агрессивная актуализация допустима только после построения полного плана, проверки cache/manifest и успешного разрешения источников.

Legacy-контент хранится в многочисленных `scaled` подпапках рядом с оригиналами. В Antenna обнаружено:

- 162 legacy-каталога с именем `scaled`;
- 0 новых каталогов `psr_scaled` на момент инвентаризации;
- 6827 companion-файлов с `_scaled_XXX`;
- 157 каталогов, содержащих такие файлы.

Legacy-cleanup — отдельная операция. Она должна сначала построить инвентарь ссылок из известных VMF/cache и вывести dry-run. Обычный запуск карты не должен удалять legacy-файлы.

Отдельная проблема миграции: старые карты могут использовать `_scaled_XXX` или `_static` как `model` новой `prop_static_scalable`. В force-run 1.1.2 такие модели могли удаляться до поиска оригинала. Нужна явная политика миграции, а не эвристика по строке имени.

## Релиз 1.1.2 как baseline

Рабочий pipeline 1.1.2:

1. Regex-парсинг PSR entities.
2. Сравнение с pickle cache.
3. Поиск модели в проекте, SearchPaths и VPK.
4. Извлечение и декомпиляция Crowbar.
5. Копирование/масштабирование QC.
6. Компиляция studiomdl.
7. Regex-замена entity на `prop_static` в копии VMF.

Сильная сторона — накопленные workaround'ы для реальных Source-моделей. Слабые стороны:

- порядок-зависимый regex для VMF;
- неоднозначный поиск по basename;
- отсутствие транзакции между компиляцией и VMF;
- cache со списками scales/colors вместо identities конкретных результатов;
- широкое удаление по имени;
- basename-коллизии временных папок;
- частые записи cache;
- отсутствие автоматических тестов.

Покраска в 1.1.2 не реализована: `rendercolor` и `skin` читаются и частично кэшируются, но VMT и skin families не создаются.

## Benchmark 1.1.2

Референсный лог:

```text
debug_logs/psr_big_map_test_02a_props_scaling_recompiler_log.txt
```

Условия:

- свежая установка PSR 1.1.2;
- cache отсутствует;
- force recompile;
- карта `psr_big_map_test_02a`, рекордная по количеству `prop_static_scalable`;
- реальный проект Antenna.

Показатели:

| Метрика | Значение |
|---|---:|
| PSR entities | 2230 |
| Уникальные исходные модели | 133 |
| Запуски декомпиляции | 130 |
| Успешно завершённые QC compilation | 149 |
| Уже static при scale 1.0 | 49 |
| Реально не найденные VPK models | 3 |
| Записи cache | 199 |
| KeyValues warnings от studiomdl | 104 |
| Уникальные VMT, породившие KeyValues warnings | 9 |
| 2D geometry warnings | 20 |
| Convex fallback warnings | 9 |
| Явные compilation/decompilation failures | 0 |
| Полное время | 26 минут 1.04 секунды |

Наблюдения:

- 2230 entity requests агрегируются в сравнительно небольшое число исходников и артефактов. Планирование должно работать по уникальным requests, не по сущностям.
- 199 cache writes соответствуют initial save плюс почти каждому результату/skip. В 2.0 нужна одна атомарная commit-запись либо редкие checkpoint'ы.
- 104 предупреждения относятся всего к девяти VMT и многократно повторяются при компиляции вариантов. Итоговый отчёт должен дедуплицировать их по типу, файлу и сообщению.
- Три пропущенных модели: `buterbrod_scaled_150.mdl`, `carparts_tire01a_static.mdl`, `aperture_supply_crate02_static.mdl`. Это legacy-generated имена, использованные как входные модели и удалённые/не найденные при force-run.
- В логе нет явных failed compilation, но существование строки `Completed` недостаточно: 2.0 должен дополнительно проверять полный набор выходных companion-файлов.

Этот лог — baseline для будущего сравнения производительности и качества диагностики. Сравнивать следует отдельно discovery/planning, source resolution, decompilation, material generation, model compilation, validation и VMF commit.

## Полезные части прототипа 2.0

- dataclass-направление для описания проектов и ассетов;
- единый `RecompilerApp`;
- нормальный logging с итоговым отчётом;
- одноразовый индекс проектных файлов;
- предварительное чтение VPK trees;
- чтение MDL static flag/material metadata;
- идея hash-based invalidation;
- учёт использования вариантов по картам;
- временные каталоги на основе полного logical path вместо basename.

Эти идеи следует переносить после уточнения модели данных и создания тестов, а не сохранять текущую реализацию буквально.

## Известные дефекты прототипа 2.0

- не создаёт `vmf_out` при нуле активных сущностей;
- считает карту только со скрытыми PSR entities ошибкой;
- `remove_unused` и `check_origs` не реализованы;
- `copy_and_modify_orig_qc` и `compile_folder` — заглушки;
- компиляция отключена;
- cache не сохраняется;
- есть блокирующие debug `input()`;
- VPK lookup использует basename substring и может вернуть ложный архив;
- folder index ориентирован на game directory и плохо работает с внешними SearchPaths;
- material lookup продолжает поиск после успеха и нарушает приоритет SearchPaths;
- skin change detection сравнивает в основном длину и содержит ошибочное определение `_col_`;
- нет валидации skin index/RGB и канонизации request identity;
- `set` создаёт недетерминированный порядок будущих цветных skins;
- финальный logger может сообщить об успешном завершении после ошибки.

## Требуемая стратегия тестирования

До крупного рефакторинга нужны маленькие фиксированные fixtures:

- VMF: обычные, hidden, вложенные блоки, другой порядок ключей, комментарии, повторяющиеся ключи, отсутствие PSR entities;
- GameInfo: все реально используемые виды SearchPaths и конфликт одного logical path в нескольких источниках;
- VPK index: точный путь, одинаковые basename, отсутствующие optional companions;
- QC: static/dynamic/physics, `$scale`, LOD, collision, skin families, комментарии и разные formatting styles;
- VMT: полные материалы и Patch matrix;
- MDL: поддерживаемые версии, static flag, несколько `$cdmaterials`, несколько skins, повреждённые offsets;
- cache: cold start, warm start, missing artifact, modified source, schema migration, interrupted write;
- project isolation: две разные GameInfo identity с совпадающими именами карт и logical asset paths не разделяют cache records, `MapUsage`, cleanup-план или блокировку;
- scale: production/cache сохраняют raw `modelscale` и PSR compile scale, но не Hammer-compatible effective scale; operation/QC plan отдельно выводит geometry scale из bone count/static flag; parsing и geometry tests доказывают Hammer-совместимую нормализацию, а значения ниже `0.01` после каждой применимой стадии детерминированно клампятся и дают диагностику;
- end-to-end: no-op VMF, один static 1.0 white, dynamic 1.0, scaled static, colored static и сочетание нескольких карт.

Реальные VMF теперь доступны в проекте Antenna и описаны ниже. Они остаются mutable integration-окружением вне репозитория. Для unit/regression automation позднее нужно сделать минимальные source-preserving копии или synthetic fixtures с зафиксированным provenance/hash, не изменяя оригинальные карты.

Read-only проверка нового VMF discovery воспроизвела зафиксированные counts и SHA-256 всех трёх приоритетных карт: 27/118/49 активных PSR entities, ноль hidden и ноль structural diagnostics. Для `aa_models_color_tint_test_01a.vmf` все восемь уникальных моделей дополнительно разрешены и прочитаны через production MDL adapter без diagnostics. После подключения scale resolver полный pre-generation plan этой карты также валиден: 27 usages агрегированы в 17 generated-model requirements и 8 color/skin requirements без diagnostics.

Read-only material inventory той же `aa_models_color_tint_test_01a.vmf` при неизменном SHA-256 `18AEDE35A65477A3CECD00B6E063DE3E5807F5FB7388DD77C37F80958F57B69D` нашёл четыре уникальных реально требуемых VMT и восемь `(source VMT, RGB)` generated identities. Все четыре VMT разрешены из folder SearchPath, используют прямой `VertexLitGeneric`, не содержат `$color`/`$color2`, proxies или Patch dependencies. Текущий консервативный план для всех восьми identities валиден и выбирает `$color2` + `insert` + `patch`; внешние файлы не изменялись.

Cold-cache skin-layout inventory этой карты построил восемь model layouts и 27 final entity assignments без ошибок. Восемь colored mappings распределены между `airplane_funal_parachute` (исходный skin 0, цветной index 1) и `book_2` (восемь исходных rows 0–7, семь цветных rows 8–14). Для `book_2` mappings отсортированы по `(source skin, RGB)`, поэтому skins 0/1/2 и все запрошенные цвета получают воспроизводимые индексы; Antenna и manifest при inventory не изменялись.

Обновлённая `psr_scale_compatibility_01a.vmf` структурно валидна: 51 active request, 63 081 байт, SHA-256 `bb6766854efb6584a7a8bd37e64e24212490c702082eb2946b6b9790b20071ee`. Первые 35 `fsmit01` cases сохраняют parsing oracle; ещё 16 requests подтверждают линейную static/multi-bone и квадратичную one-bone non-static ветви на `fsmit01`, `monitor01`, `doll01` и `door02_double`. Read-only integration plan разрешил шесть исходных MDL, построил 51 usage и 21 generated identity без ошибок; все 51 рассчитанных geometry scale точно совпали с `debug_string`. 23 ожидаемых warnings относятся к parsing/fallback/clamp/rounding исходной строковой матрицы. Внешняя карта и ассеты не изменялись.

Отдельная исследовательская карта `psr_scale_compatibility_01a.vmf` используется для эмпирического определения Hammer++-совместимого scale. В её `prop_static_scalable` поле `debug_string` хранит test oracle в формате `effective_scale=<value>` для каждого raw `modelscale`. Oracle соответствует ожидаемому geometry scale PSR: для обычной ветви он совпадает с compile identity, для one-bone non-static отражает квадрат, а значения ниже продуктового минимума клампятся до `0.01`. Это тестовая аннотация, а не поле production/cache schema. Актуальный структурно проверенный снимок и список незакрытых случаев находятся в `docs/research/HAMMERPP_SCALE_COMPATIBILITY.md`. Карта является источником наблюдений, но не должна изменяться автоматическими тестами PSR.

## Реальный regression-набор VMF

Основной каталог:

```text
C:\Program Files (x86)\Steam\steamapps\sourcemods\antenna_sdk2013\maps
```

Приоритетные исходные карты структурно валидны и не содержат hidden entities. Последнее означает, что hidden-сценарии всё ещё требуют отдельного synthetic fixture.

| Карта | SHA-256 | PSR entities | Модели | Уникальные requests | Повторы | Non-white |
|---|---|---:|---:|---:|---:|---:|
| `aa_models_color_tint_test_01a.vmf` | `18AEDE35A65477A3CECD00B6E063DE3E5807F5FB7388DD77C37F80958F57B69D` | 27 | 8 | 25 | 2 | 8 |
| `aa_models_static_convert_test_01a.vmf` | `690C587A6D9C6FF50AA951A997BFA02E1B8DF896EF40B711DA090F9581EEAE4A` | 118 | 66 | 107 | 11 | 6 |
| `psr_test_01a.vmf` | `506DA823F25275C40B0DFEA55F2F891A893626E9EB71F47D48865B376B94391A` | 49 | 7 | 49 | 0 | 24 |

Здесь request — точная raw-комбинация `(model, modelscale, skin, rendercolor)` до Hammer-compatible нормализации. Повтор — дополнительная entity с уже встречавшейся raw-комбинацией. После вычисления effective scale и итогового compile scale число generated artifacts может быть меньше.

### aa_models_color_tint_test_01a

Главная integration-карта покраски и взаимодействия покраски со scale/static conversion.

- 27 PSR entities и 12 side-by-side `prop_scalable` reference entities.
- Восемь non-white entities используют четыре цвета: `190 48 148`, `228 0 228`, `76 146 211`, `86 202 181`.
- `models/props_se/storage/book_2.mdl` представлен 11 сущностями, исходными skins 0/1/2, четырьмя цветами и восемью масштабами.
- Для `book_2` есть разные цвета одного исходного skin и один scale `2.65` с white/magenta и другим цветовым запросом на том же исходном skin. Это важная проверка того, что цвет не входит в identity GeneratedModel, а становится отдельным SkinMapping/ColoredMaterial.
- `models/props_se/airplane_funal_parachute.mdl` проверяет white и blue при scale 1.0.
- 12 entities содержат legacy-key `convert_prop_to_static`; 2.0 не должен зависеть от порядка или наличия этого ключа.
- Невалидных scale/RGB и generated `_scaled_` inputs не обнаружено.

### aa_models_static_convert_test_01a

Широкая compatibility- и diagnostics-карта, а не набор только корректных happy paths.

- 118 PSR entities, 66 моделей и 36 side-by-side `prop_scalable` references.
- Охватывает static, dynamic, physics и проблемные реальные модели, масштабы меньше/равно/больше 1.0, source skins 0/1/2 и несколько non-white запросов.
- Содержит восемь Hammer++ scale-compatibility probes: шесть raw-значений с запятой (`0,8`/`0,4`), `sfgsfg` и `0.009` ниже старого минимального порога. Их нельзя заранее классифицировать как ошибки только по синтаксису строки.
- Содержит три намеренно повторно масштабированных `_scaled_` model path, включая цепочку из трёх `_scaled_` суффиксов. Это legacy/diagnostic input для явной политики отказа или миграции.
- Имена, заканчивающиеся на `_static.mdl`, сами по себе не являются доказательством generated-ассета и не должны интерпретироваться специально.
- 59 entities содержат legacy-key `convert_prop_to_static`.
- Версия после старого instance preprocessing находится в `maps/inst_fix/aa_models_static_convert_test_01a.vmf`: она также валидна, но содержит 112 PSR entities и не включает часть позднее добавленных Hammer++ compatibility-тестов. Основным источником считать root VMF.

Ожидаемый effective scale для этих сущностей определяется Hammer++, а не Python `float()` и не старым порогом PSR. Подтверждено: `blablabla` и `1,0` видны как 1.0, `3,0` — как 3.0. Остальные границы и необычные строки нужно фиксировать по реальному viewport; если Hammer++ показывает 1.0, это не аварийный fallback, а правильный effective scale. После этого PSR применяет единственное отдельное правило: effective scale ниже `0.01` компилируется как `0.01`.

### psr_test_01a

Компактная regression-карта для scale/static conversion, Hammer++ scale parsing и покраски при исходном skin 0.

- 49 PSR entities, 7 моделей и 49 уникальных raw requests; точных raw-повторов в текущем снимке нет.
- Все 49 entities используют source skin 0. Поэтому карта хорошо проверяет покраску, но не заменяет `aa_models_color_tint_test_01a` для remap нескольих исходных skin families.
- 24 non-white entities охватывают девять non-white RGB; вместе с white это десять цветовых значений.
- Raw scale matrix: `1.0` (15), `0.50` (13), `1.50` (15), `3` (2), а также по одной entity с `1,0`, `3,0`, `invalid_scale_test` и `blablabla`.
- По поведению output 1.1.2 три модели при scale 1.0 используют оригинал как static, четыре получают legacy `_static` вариант. Это удобная начальная матрица уже-static против convert-to-static.

Legacy-output 1.1.2 находится в `maps/psr_temp/psr_test_01a.vmf` и структурно валиден:

- все 45 source entity ID сохранены;
- все 45 `prop_static_scalable` превращены в `prop_static`;
- получилось 21 уникальное output model path;
- source `modelscale`, `rendercolor` и `skin` остались в entity, хотя model path уже указывает на физически масштабированную модель;
- output старше текущего root VMF, поэтому является историческим примером, а не полным oracle текущего входа.

Решение 2.0: из итогового `prop_static` удаляются PSR-only keys — raw `modelscale`, `rendercolor`, legacy `convert_prop_to_static` и будущие служебные поля PSR. `skin` не является PSR-only property: он нужен `prop_static` и записывается как итоговый mapped skin index, чтобы выбрать исходную или generated color skin family.

### Роль legacy-файлов рядом с картами

- `.vmx`, `_backup.vmf`, `inst_fix/**` и `psr_temp/**` сохранять как исторические артефакты и использовать только при явном сравнении.
- Root `.vmf` трёх приоритетных карт является текущим integration input.
- Не переписывать эти VMF при автоматизации. Для destructive/end-to-end теста сначала делать отдельную копию и задавать отдельный `vmf_out`.
- Hash в таблице служит provenance текущего снимка, но карты пользователь может дальше редактировать; изменение hash требует повторной инвентаризации, а не автоматического отказа от тестирования.

## Открытые решения

- расширение Hammer++ regression-матрицы редкими формами и верхними пределами;
- расширение подтверждённой SDK-матрицы `$color`/`$color2` за пределы `VertexLitGeneric`/`UnlitGeneric` и материалов с уже существующим color-key;
- точная runtime-семантика generated Patch `insert`/`replace` и Patch-chain на SDK 2013 SP;
- способ fingerprint зависимостей для Patch из VPK;
- политика переноса legacy-generated модели, выбранной как новый original;
- обработка частичного успеха, когда одна из моделей карты не компилируется;
- допустимость параллельного запуска двух Hammer compile jobs для одного проекта;
- окончательное CLI-место staging parent и retention policy для failed runs;
- точная политика cleanup для старых ревизий generated skin layout.

## Принятые решения

- Оригиналы неизменяемы.
- Generated-модели живут в `models/psr_scaled`.
- Generated-материалы живут в `materials/models/psr_scaled`.
- Используется только `_scaled_XXX`; `_static` не создаётся и не трактуется специально.
- Нейтральный static 1.0 использует оригинал; остальные случаи создают managed-модель.
- Effective scale повторяет видимое поведение Hammer++, включая его обработку необычных raw-строк.
- PSR compile scale получается из effective scale нижним clamp `0.01` и decimal `ROUND_HALF_UP` до сотых; generated identity и `_scaled_XXX` используют этот compile scale и точный целый процент с минимальной шириной 3 в имени.
- Geometry scale равен `compile_scale²` только для one-bone non-static source MDL и равен compile scale для static/multi-bone source; `prop_data` и PHY не переключают режим. Geometry ниже `0.01` клампится отдельно. Geometry присутствует в transient plan/QC artifacts, но не заменяет compile identity в кэше.
- Из итогового `prop_static` удаляются PSR-only properties; `skin` сохраняется как итоговый mapped index.
- Normal cleanup управляет только новыми managed roots.
- Legacy migration/cleanup является отдельной операцией.
- Покраска предпочитает Patch с fallback на полную VMT-копию.
- Project cache использует строго валидируемый versioned JSON manifest, изолированный нормализованной identity `GameInfo.txt`, и атомарный replace вместо pickle.
- QC variants строятся из общего validated reference QC; generated filename использует compile-scale identity, `$scale` и LOD distances используют model-dependent geometry scale, collision и bounds сохраняются без числового переписывания. Compile compatibility этого правила подтверждена текущей staged SDK matrix; viewport regression остаётся отдельным более широким уровнем проверки.
- Staging является operation-scoped и caller-owned: уникальный marker-protected root создаётся под явно заданным parent, source content повторно проверяется перед materialization, обычный cleanup не выходит за этот root. Неудачный run может явно сохранить staging для диагностики.
- Для SDK 2013 SP ожидаемый compiled model set — `.mdl`, `.vvd`, `.dx80.vtx`, `.dx90.vtx`, `.sw.vtx` и `.phy` при наличии collision. Exit code/строка `Completed` недостаточны: до commit обязательны проверка каждого файла, managed internal model name и static-prop flag MDL.
- Сохранение `$collisionmodel`/`$collisionjoints`, explicit bounds и замена pre-existing `$scale` подтверждены staged compile-validation на четырёх реальных cases. Специальные числовые переписывания collision/bounds и прежнее массовое комментирование `$bbox`/`$definebone` не требуются для подтверждённой матрицы.
- Первый 2.0 ограничен Source SDK 2013 SP.
- Архитектурная память хранится в этом документе, обязательные рабочие правила — в корневом `AGENTS.md`.
