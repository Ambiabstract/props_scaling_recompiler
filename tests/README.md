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

`test_srctools_vmt.py` проверяет semantic metadata обычных VMT, существующего color-key, proxies и Patch dependencies в folder/VPK. Material phase остаётся read-only: он назначает generated VMT paths и режим `patch`/`full_copy`, но не пишет материалы и не назначает skin index до появления стабильного cache-backed layout.

`test_srctools_searchpaths.py` создаёт маленькие folder/VPK деревья только внутри системного temporary directory. Он проверяет строгий source order, exact logical path, provenance, `_dir.vpk` resolution и детерминированное раскрытие wildcard без обращения к SDK или пользовательскому проекту.

`test_vmf_discovery_planning.py` использует байтовые VMF fixtures и синтетические MDL только во временном project root. Он проверяет, что hidden/nested данные не становятся активными requests, raw scale сохраняется отдельно от resolved compile scale, а pre-generation plan агрегирует model и color identities независимо и не записывает VMF. `test_scale_fixture.py` прогоняет production resolver по всем 35 подтверждённым Hammer++ cases и проверяет `ROUND_HALF_UP`/`_scaled_XXX` naming, включая минимальную ширину 3 (`001`, `050`) без ограничения более длинных процентов.
