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

## Структура нового кода

- `psr/domain` — чистые identities и operation plan без filesystem side effects;
- `psr/keyvalues` — source-preserving Valve KeyValues parser и targeted edits;
- `psr/assets` — SearchPaths/VPK, MDL/QC/VMT и adapters внешних инструментов;
- `psr/cache` — project-scoped manifest, migrations и атомарная запись;
- `psr/pipeline` — стадии `discover -> plan -> generate -> validate -> commit`;
- `tests/fixtures` — маленькие synthetic и provenance-tracked regression inputs.

Корневые `props_scaling_recompiler.py` и `props_scaling_recompiler_v1.1.2.py` пока не являются entry point нового пакета: первый остаётся незавершённым прототипом, второй — поведенческим baseline.

## Уровни тестов

- Unit/contract tests всегда изолированы от установленного SDK и проектов пользователя.
- Маркер `integration` предназначен для тестов на временном synthetic project root.
- Маркер `external_sdk` является opt-in и никогда не разрешает изменение оригинальных файлов Antenna.
- VMT/Patch research и fixtures добавляются ближе к реализации покраски.
