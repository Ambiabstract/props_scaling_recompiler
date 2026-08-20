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

Высокоуровневый `srctools.game.Game.get_filesystem()` не использовать: он не сохраняет требуемый PSR порядок folder/VPK SearchPaths. `FileSystemChain` собирается вручную из `RawFileSystem` и `VPKFileSystem`. `srctools.vmf.VMF.export()` также не используется для итоговой записи VMF; source-preserving edits остаются ответственностью `psr/keyvalues`.

Исходные модели инспектируются через `psr.assets.inspect_source_model()`. Функция принимает только логический `models/**/*.mdl` path, запрещает managed namespace `models/psr_scaled/**`, разрешает модель, companions и VMT через одну ordered filesystem chain и возвращает immutable discovery DTO. Порядок material slots берётся из отсортированных числовых индексов MDL mesh table, а не из внутреннего `set` в `srctools.mdl.Model`.

## Уровни тестов

- Unit/contract tests всегда изолированы от установленного SDK и проектов пользователя.
- Synthetic MDL cases описаны JSON-метаданными; минимальные MDL/PHY и VPK строятся детерминированно только во временных каталогах тестов.
- Маркер `integration` предназначен для тестов на временном synthetic project root.
- Маркер `external_sdk` является opt-in и никогда не разрешает изменение оригинальных файлов Antenna.
- VMT/Patch research и fixtures добавляются ближе к реализации покраски.
