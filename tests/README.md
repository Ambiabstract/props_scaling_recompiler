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

`test_cache_manifest.py` проверяет project isolation, schema v0→v1 migration, строгий round-trip всех раздельных таблиц, corrupt/incompatible recovery и сохранение старого файла при сорванном atomic replace. `test_skin_layout.py` проверяет cold/warm layout, стабильность индексов, независимость от порядка entity requests, invalidation при изменении исходной skin table, отсутствие effective scale в `MapUsage` и no-op reanalysis.

`test_srctools_searchpaths.py` создаёт маленькие folder/VPK деревья только внутри системного temporary directory. Он проверяет строгий source order, exact logical path, provenance, `_dir.vpk` resolution и детерминированное раскрытие wildcard без обращения к SDK или пользовательскому проекту.

`test_vmf_discovery_planning.py` использует байтовые VMF fixtures и синтетические MDL только во временном project root. Он проверяет, что hidden/nested данные не становятся активными requests, raw scale сохраняется отдельно от resolved compile scale, model-dependent geometry различает one-bone non-static и multi-bone/static sources, а pre-generation plan агрегирует model и color identities независимо и не записывает VMF. `test_scale_fixture.py` прогоняет parsing resolver по 35 подтверждённым Hammer++ cases и geometry resolver по всем 51 cases обновлённого oracle, а также проверяет `ROUND_HALF_UP`/`_scaled_XXX` naming и оба нижних clamp.

`test_staging_toolchain.py` проверяет operation-scoped staging root, защиту от path escape/conflicting writes, повторную сверку provenance/hash перед materialization, безопасный cleanup, argv-only adapters Crowbar/StudioMDL и обязательную post-compile проверку managed path, MDL header/static flag и полного набора SDK 2013 SP companions. Встроенная QC-матрица перекрёстно покрывает static/dynamic, отсутствие collision/`$collisionmodel`/`$collisionjoints` и наличие/отсутствие исходного `$scale` без обращения к внешнему SDK.

`test_generation_pipeline.py` проверяет полный staged `generate -> validate` slice через fake subprocess tools: детерминированные VMT, одну декомпиляцию source model для нескольких масштабов, compile-ready QC рядом с Crowbar output, полную проверку companions, no-op и failure containment. Тесты не публикуют файлы в проект и не обращаются к внешнему SDK.
