# PSR 2.0 tests

Тесты разделены по уровню риска и зависимости от внешней среды:

- обычные unit/contract tests используют только содержимое `tests/fixtures` и запускаются по умолчанию;
- `integration` предназначен для изолированного временного проекта и локальных tool adapters;
- `external_sdk` требует явного opt-in и установленного Source SDK 2013 SP;
- реальные проекты и исходные карты, включая Antenna, никогда не изменяются тестами.

Основная команда после установки test dependencies:

```text
python -m pytest
```

Текущий начальный набор также совместим со стандартной библиотекой и проверяется без pytest:

```text
python -m unittest discover -s tests -v
```

VMT/Patch fixtures и SDK-эксперименты намеренно отложены до этапа реализации покраски.

`test_srctools_searchpaths.py` создаёт маленькие folder/VPK деревья только внутри системного temporary directory. Он проверяет строгий source order, exact logical path, provenance, `_dir.vpk` resolution и детерминированное раскрытие wildcard без обращения к SDK или пользовательскому проекту.
