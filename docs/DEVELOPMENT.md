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
- `tests/fixtures` — маленькие synthetic и provenance-tracked regression inputs.

Корневые `props_scaling_recompiler.py` и `props_scaling_recompiler_v1.1.2.py` пока не являются entry point нового пакета: первый остаётся незавершённым прототипом, второй — поведенческим baseline.

Внешние model tools вызываются только через `psr.assets.toolchain`: argv передаётся списком без shell, stdout/stderr сохраняются как bytes, timeout и ненулевой exit code становятся категоризированными ошибками. Crowbar обязан выдать ровно один QC в пустом isolated output directory. Успех StudioMDL сам по себе не считается готовым артефактом: `validate_compiled_model()` отдельно проверяет managed logical path, MDL 44–49, точный internal model name, static-prop flag и `.mdl/.vvd/.dx80.vtx/.dx90.vtx/.sw.vtx`, а при collision также `.phy`.

`psr.assets.generate_colored_material()` детерминированно создаёт generated Patch либо раскрытую full-copy VMT, повторно парсит собственный output через `srctools.vmt.Material` и возвращает bytes вместе с SHA-256. Patch включает полный logical source VMT path и помещает выбранный `$color`/`$color2` в запланированный `insert`/`replace`; full-copy сохраняет effective shader, параметры, blocks и proxies. Синтаксический/semantic контракт автоматизирован, но runtime-семантика Patch всё ещё требует отдельной opt-in проверки на SDK 2013 SP перед production commit цветных материалов.

`psr.pipeline.generate_and_validate()` связывает валидные operation/material/skin plans только внутри caller-owned `StagingWorkspace`. Он повторно инспектирует VMT dependency graph, материализует и сверяет исходные MDL/companions, декомпилирует каждый source model ровно один раз, строит QC plan, размещает compile-ready variant QC рядом с Crowbar QC для сохранения относительных SMD/include paths, запускает StudioMDL и проверяет полный generated companion set. Результат содержит только validated staged artifacts и ничего не публикует в проект, manifest или VMF.

`psr.pipeline.StagingWorkspace` создаёт уникальный каталог из operation identity и случайного suffix под явно переданным parent. Все записи ограничены этим root; source files перед materialization повторно сверяются с discovery provenance/size/SHA-256. Cleanup разрешён только для собственного каталога с marker-файлом. Режим `preserve=True` оставляет staging для диагностики и требует явного cleanup.

Высокоуровневый `srctools.game.Game.get_filesystem()` не использовать: он не сохраняет требуемый PSR порядок folder/VPK SearchPaths. `FileSystemChain` собирается вручную из `RawFileSystem` и `VPKFileSystem`. `srctools.vmf.VMF.export()` также не используется для итоговой записи VMF; source-preserving edits остаются ответственностью `psr/keyvalues`.

Исходные модели инспектируются через `psr.assets.inspect_source_model()`. Функция принимает только логический `models/**/*.mdl` path, запрещает managed namespace `models/psr_scaled/**`, разрешает модель, companions и VMT через одну ordered filesystem chain и возвращает immutable discovery DTO. Порядок material slots берётся из отсортированных числовых индексов MDL mesh table, а не из внутреннего `set` в `srctools.mdl.Model`.

VMF читается как bytes через `psr.keyvalues.parse_vmf()`. Parser сохраняет исходный документ и source spans, не схлопывает повторяющиеся keys и никогда не принимает nested `editor` property за direct entity property. Текущий этап является read-only: `discover_vmf_requests()` собирает активные top-level requests, `inspect_map_sources()` разрешает уникальные MDL, а чистая `build_operation_plan()` вызывает `psr.domain.resolve_compile_scale()` и агрегирует операции. Parsing resolver сохраняет raw `modelscale`, применяет clamp и decimal `ROUND_HALF_UP` до сотых, возвращает итоговый `Decimal` compile scale и объясняет преобразования через warnings. Затем `psr.domain.resolve_geometry_scale()` использует MDL bone count/static flag: one-bone non-static получает квадрат, multi-bone/static — линейный масштаб; geometry ниже `0.01` отдельно клампится. `psr.domain.scaled_model_path()` назначает output по линейной compile identity под `models/psr_scaled/` с точным целым процентом `_scaled_XXX`, отформатированным с минимальной шириной 3 (`001`, `050`, `100`, `1000`).

## Уровни тестов

- Unit/contract tests всегда изолированы от установленного SDK и проектов пользователя.
- Synthetic MDL cases описаны JSON-метаданными; минимальные MDL/PHY и VPK строятся детерминированно только во временных каталогах тестов.
- VMF discovery/planning contract-тесты проверяют direct/nested scope, hidden entities, repeated keys, malformed syntax, no-op output intent, source inspection, model/color aggregation и накопление diagnostics.
- Маркер `integration` предназначен для тестов на временном synthetic project root.
- Маркер `external_sdk` является opt-in и никогда не разрешает изменение оригинальных файлов Antenna.
- VMT/Patch fixtures проверяют generated semantic round-trip; runtime Patch-проверка на SDK остаётся отдельным opt-in уровнем.
