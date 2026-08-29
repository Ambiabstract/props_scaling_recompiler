# Разработка PSR 2.0

## Python-окружение

Минимальная поддерживаемая версия Python — 3.10. Рекомендуемый локальный setup:

```text
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest
```

Начальные contract-тесты не зависят от pytest-specific API и могут быть запущены без установки дополнительных пакетов:

```text
python -m unittest discover -s tests -v
```

Runtime-зависимость `srctools` закреплена точной версией `2.7.0`. Глобальная установка разработчика не считается воспроизводимым окружением: editable install должен установить ту же версию в `.venv`.

## Структура нового кода

- `psr/domain` — чистые identities и operation plan без filesystem side effects;
- `psr/keyvalues` — source-preserving Valve KeyValues parser и targeted edits;
- `psr/assets` — собственная ordered SearchPaths policy поверх `srctools.filesys`, MDL/VMT adapters, QC и adapters внешних инструментов;
- `psr/cache` — project-scoped manifest, migrations и атомарная запись;
- `psr/pipeline` — стадии `discover -> plan -> generate -> validate -> commit`;
- `psr/runtime` — project state/lock, recovery journal, coordinator и общий diagnostic report;
- `psr/cli.py`, `psr_entrypoint.py` — production console entry point;
- `packaging` — one-file PyInstaller spec, hook и release script;
- `tests/fixtures` — маленькие synthetic и provenance-tracked regression inputs.

Корневые `props_scaling_recompiler.py` и `props_scaling_recompiler_v1.1.2.py` не являются entry point нового пакета: первый остаётся незавершённым прототипом, второй — поведенческим baseline. Production entry point — `psr_entrypoint.py`/`psr.cli`.

## Runtime и release-сборка

CLI сохраняет совместимость с Hammer compile-run через `-game`, `-vmf_in` и `-vmf_out`. Старые `0/1`-флаги принимаются, но игнорируются с deprecated warning. Постоянный project state размещается под `%LOCALAPPDATA%\PropsScalingRecompiler\projects\<project_id>`; два одновременных запуска одного проекта блокируются. Короткий operation staging находится под `%LOCALAPPDATA%\PropsScalingRecompiler\work\<project_prefix>`, чтобы Crowbar 0.68 не упирался в legacy Win32 path limit. Manifest, recovery journal, logs и staging не размещаются рядом с пользовательским VMF.

Fallback версии 2.0 для dynamic MDL с пустым option многовариантного bodygroup является чистой VMF-конверсией: asset не передаётся Crowbar/StudioMDL, не получает managed MDL/VMT/skin layout и не восстанавливается из прежних cached variants. В output сохраняются исходные `model`, `modelscale`, `rendercolor` и `skin`, classname становится `prop_dynamic`, а legacy `convert_prop_to_static` удаляется. Полноценная static-конвертация этого класса моделей отложена в отдельный будущий SMD-aware этап; zero-triangle placeholders запрещены.

Целевая поставка — Windows 10/11 x64: один `props_scaling_recompiler.exe` и отдельный `third-party/CrowbarCommandLineDecomp.exe`. VPKEdit и Crowbar не встраиваются в основной exe. StudioMDL берётся из окружения Source SDK 2013 SP. Текущая сборка `2.0.0.dev3` после исправления dynamic-to-static bone layout — 11 433 757 байт (10,90 MiB), SHA-256 `A131E91A9A1928D33924CECB4EBAF97738FA80BEE581EA3C4DF37A99025D086C`.

Воспроизводимая сборка:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build_release.ps1 -CrowbarPath <path-to-CrowbarCommandLineDecomp.exe>
```

Скрипт сначала запускает обычные тесты, пересоздаёт только собственный `dist/props_scaling_recompiler_v2`, выполняет frozen `--version` smoke и отклоняет основной exe крупнее 64 MiB. Размер свыше предпочтительных 16 MiB даёт warning. Текущая проверенная сборка `2.0.0.dev3` на Python 3.14/PyInstaller 6.22.2 — 11 433 757 байт (10,90 MiB), SHA-256 `A131E91A9A1928D33924CECB4EBAF97738FA80BEE581EA3C4DF37A99025D086C`.

После каждого полезного изменения рабочего кода, поведения или сборочной конфигурации PSR завершённый цикл разработки включает не только автоматические проверки, но и немедленную подготовку версии для ручного теста пользователя:

1. Успешно выполнить release-сборку и frozen smoke.
2. Скопировать полученный `dist\props_scaling_recompiler_v2\props_scaling_recompiler.exe` с заменой в `C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin\props_scaling_recompiler.exe`.
3. Повторно вычислить SHA-256 обоих файлов и убедиться, что установленный EXE побайтно соответствует только что собранному.

Пользователь заранее разрешил это точечное обновление установленного EXE, чтобы сразу переходить к ручной проверке. Разрешение не распространяется на другие файлы SDK или проекта Antenna. Изменения только документации не требуют пересборки и установки EXE.

Полный frozen no-op regression запускается отдельно, чтобы тестировать уже собранный файл:

```text
$env:PSR_FROZEN_EXE = (Resolve-Path dist\props_scaling_recompiler_v2\props_scaling_recompiler.exe).Path
python -m pytest -q tests/test_frozen_executable.py
```

Полный opt-in production runtime regression на копии приоритетной карты Antenna запускает cold generation реальными Crowbar/StudioMDL, затем tool-less warm reuse. Протокол и зафиксированные метрики находятся в `docs/research/PRODUCTION_RUNTIME_VALIDATION.md`:

```text
$env:PSR_RUN_EXTERNAL_RUNTIME = '1'
python -m pytest -q -s tests/test_external_runtime.py -m external_sdk
```

Внешние model tools вызываются только через `psr.assets.toolchain`: argv передаётся списком без shell, stdout/stderr сохраняются как bytes, timeout и ненулевой exit code становятся категоризированными ошибками. Crowbar обязан выдать ровно один QC в пустом isolated output directory. Успех StudioMDL сам по себе не считается готовым артефактом: `validate_compiled_model()` отдельно проверяет managed logical path, MDL 44–49, точное StudioMDL-представимое internal model name, static-prop flag и `.mdl/.vvd/.dx80.vtx/.dx90.vtx/.sw.vtx`, а при collision также `.phy`. Поле `studiohdr_t::name[64]` в реальном SDK хранит не более 63 ASCII-байт и завершающий NUL, поэтому для более длинного `$modelname` строго проверяется детерминированный 63-байтовый префикс полного logical path.

`psr.assets.generate_colored_material()` детерминированно создаёт generated Patch либо раскрытую full-copy VMT, повторно парсит собственный output через `srctools.vmt.Material` и возвращает bytes вместе с SHA-256. Patch включает полный logical source VMT path и помещает выбранный `$color`/`$color2` в запланированный `insert`/`replace`; full-copy сохраняет effective shader, параметры, blocks и proxies. Синтаксический/semantic контракт автоматизирован, но runtime-семантика Patch всё ещё требует отдельной opt-in проверки на SDK 2013 SP перед production commit цветных материалов.

Managed material binding при компиляции модели использует отдельный QC root `$cdmaterials "models/psr_scaled/"`. Полные logical paths layout/manifest остаются `models/psr_scaled/<source-subpath>/<name>`, но в compile-ready skin families они записываются относительно root: `<source-subpath>/<name>`. Не возвращайте полные managed paths непосредственно в QC: StudioMDL сохраняет исходные `$cdmaterials`, и движок может сложить их в ошибочный `models/<source-dir>/models/psr_scaled/...`. Изменение этого контракта требует bump `qc_material_binding_version` в skin-layout fingerprint.

`psr.pipeline.generate_and_validate()` остаётся fail-closed compatibility wrapper для прямых тестов/вызовов. Production runtime использует раздельные `generate_materials_and_validate()` и `generate_models_and_validate()`: они продолжают независимые work units, возвращают validated staged artifacts вместе с `GenerationFailure`, а outcome ledger вычисляет минимальное dependency closure и перестраивает surviving plans до commit. Никакой generation API сам не публикует файлы в проект, manifest или VMF.

Перед generation `psr.pipeline.plan_artifact_reuse()` делит surviving operation на проверенные cache hits и минимальный batch misses. Модель считается reusable только после повторного `validate_compiled_model()` и сравнения полного companion set/artifact fingerprint с текущими source/layout identities. Материал требует точного SHA-256 и совпадения VMT dependency fingerprint/режима генерации. `build_commit_plan()` объединяет только успешные generated/reused records, а `apply_commit_plan()` атомарно публикует согласованный partial result и ещё раз хэширует reused-файлы. Транзакция одного final partial plan остаётся all-or-nothing, но неудачные независимые work units до неё уже исключены outcome ledger.

`psr.pipeline.build_vmf_output()` применяет финальные model/skin assignments только к direct properties активных entity: сверяет SHA-256 и исходные spans, меняет `classname/model/skin`, для `prop_static` удаляет `modelscale`, `rendercolor` и legacy `convert_prop_to_static`, а для `reuse_dynamic` сохраняет исходные runtime properties и удаляет только legacy/service key. Отдельный `VmfFallbackAssignment` пишет `prop_dynamic_override`, побайтно сохраняет runtime properties и также удаляет только service key. Затем writer повторно структурно валидирует каждую затронутую сущность; no-op возвращает побайтно исходный VMF. `build_commit_plan()` повторно сверяет все staged hashes и полноту project-wide reconciliation, строит manifest-кандидат и VMF-кандидат. `apply_commit_plan()` сначала готовит и хэширует sibling temp-файлы, после чего устанавливает managed VMT/MDL, manifest и последним `vmf_out`; ошибка посередине восстанавливает предыдущие файлы из operation-local backups. После recoverable commit/early failure runtime атомарно доставляет структурно проверенный byte-equivalent passthrough VMF.

Перед generation `reconcile_generation_requirements()` расширяет map-local model requirements закэшированными масштабами того же source model, если изменился source fingerprint или append-only skin-layout fingerprint. Это обязательно, потому что путь `_scaled_XXX` не содержит layout revision: все масштабы проекта должны быть скомпилированы из одной новой reference skin table до общей публикации. Cache reset после изменения исходной skin table не переносит usages/scales других карт.

MDL skin metadata разделяет полную skin-reference table и `used_material_slots`. Полная таблица необходима для точного сравнения/перезаписи Crowbar QC; только mesh-used slots участвуют в поиске исходных VMT и получают colored replacements. Unused slots сохраняются в generated family без требования отсутствующих VMT.

`psr.assets.limits` фиксирует проверенные границы целевого StudioMDL: максимум 31 уникальное material name в recompilable QC и 1024 skin-family rows. `build_skin_layout_plan()` проверяет capacity перед каждым append; rejected color остаётся warning/fallback assignment на source skin, а `generate_and_validate()` материализует VMT только для принятых mappings. QC/MDL adapters повторяют защитную проверку, чтобы переполненный layout не дошёл до внешнего toolchain. Opt-in regression на реальном SDK находится в `tests/test_external_sdk_generation.py`, протокол — в `docs/research/SDK_SKIN_LIMITS.md`.

Обычный compile-run никогда не уплотняет cached colored rows: стабильность индексов важнее освобождения места после каждой правки цвета. Будущий явный cleanup должен сначала доказать отсутствие project-wide `MapUsage`, показать dry-run новой таблицы и затронутых карт/scale variants и только затем выполнять полную reconciliation/recompile. Консольный frontend обязан в любом исходе завершать запуск одним дедуплицированным блоком `ERROR / WARNING / INFO`; одинаковые причины группируют entity IDs, warning-секция выводится жёлтым, если ANSI/Windows console mode это позволяет, а exit code зависит только от доставки валидного `vmf_out`.

`psr.pipeline.StagingWorkspace` создаёт уникальный каталог из operation identity и случайного suffix под явно переданным parent. Все записи ограничены этим root; source files перед materialization повторно сверяются с discovery provenance/size/SHA-256. Cleanup разрешён только для собственного каталога с marker-файлом. Режим `preserve=True` оставляет staging для диагностики и требует явного cleanup.

Высокоуровневый `srctools.game.Game.get_filesystem()` не использовать: он не сохраняет требуемый PSR порядок folder/VPK SearchPaths. `FileSystemChain` собирается вручную из `RawFileSystem` и `VPKFileSystem`. `srctools.vmf.VMF.export()` также не используется для итоговой записи VMF; source-preserving edits остаются ответственностью `psr/keyvalues`.

Исходные модели инспектируются через `psr.assets.inspect_source_model()`. Функция принимает только логический `models/**/*.mdl` path, запрещает managed namespace `models/psr_scaled/**`, разрешает модель, companions и VMT через одну ordered filesystem chain и возвращает immutable discovery DTO. Порядок material slots берётся из отсортированных числовых индексов MDL mesh table, а не из внутреннего `set` в `srctools.mdl.Model`.

VMF читается как bytes через `psr.keyvalues.parse_vmf()`. Parser сохраняет исходный документ и source spans, не схлопывает повторяющиеся keys и никогда не принимает nested `editor` property за direct entity property. `discover_vmf_requests()` собирает активные top-level requests, `inspect_map_sources()` разрешает уникальные MDL, а чистая `build_operation_plan()` вызывает `psr.domain.resolve_compile_scale()` и агрегирует операции. Parsing resolver сохраняет raw `modelscale`, применяет clamp и decimal `ROUND_HALF_UP` до сотых, возвращает итоговый `Decimal` compile scale и объясняет преобразования через warnings. Затем `psr.domain.resolve_geometry_scale()` использует MDL bone count/static flag: one-bone non-static получает квадрат, multi-bone/static — линейный масштаб; geometry ниже `0.01` отдельно клампится. `psr.domain.scaled_model_path()` назначает output по линейной compile identity под `models/psr_scaled/` с точным целым процентом `_scaled_XXX`, отформатированным с минимальной шириной 3 (`001`, `050`, `100`, `1000`).

## Уровни тестов

- Unit/contract tests всегда изолированы от установленного SDK и проектов пользователя.
- Synthetic MDL cases описаны JSON-метаданными; минимальные MDL/PHY и VPK строятся детерминированно только во временных каталогах тестов.
- VMF discovery/planning contract-тесты проверяют direct/nested scope, hidden entities, repeated keys, malformed syntax, no-op output intent, source inspection, model/color aggregation и накопление diagnostics.
- VMF output/commit contract-тесты проверяют CRLF и untouched bytes, hidden/nested scope, вставку и remap `skin`, удаление PSR-only keys, stale input/staging/reused artifacts, no-op, manifest records и полный rollback при сбое последней замены.
- Маркер `integration` предназначен для тестов на временном synthetic project root.
- Маркер `external_sdk` является opt-in и никогда не разрешает изменение оригинальных файлов Antenna.
- VMT/Patch fixtures проверяют generated semantic round-trip; runtime Patch-проверка на SDK остаётся отдельным opt-in уровнем.
- `tests/test_external_sdk_generation.py` по `PSR_RUN_EXTERNAL_SDK=1` проверяет real `book_2` и временные VMT overlays, не изменяя Antenna/SDK. Зафиксированная compile-матрица описана в `docs/research/SDK_VMT_GENERATION_VALIDATION.md`.
