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

`test_generation_pipeline.py` проверяет полный staged `generate -> validate -> commit` slice через fake subprocess tools: детерминированные VMT, одну декомпиляцию source model для нескольких масштабов, compile-ready QC рядом с Crowbar output, полную проверку companions, manifest/VMF commit, stale-stage abort, обязательную project-wide reconciliation и rollback уже заменённых файлов при искусственном сбое. Публикация выполняется только во временный synthetic project; внешний SDK не используется. `test_vmf_output.py` отдельно фиксирует source-preserving direct-property edits, hidden/nested byte preservation, CRLF, missing/existing `skin`, no-op и отказ при изменившемся input hash.

`test_runtime_foundation.py` проверяет project-scoped `%LOCALAPPDATA%` layout, межпроцессную блокировку и дедуплицированный отчёт. `test_commit_recovery.py` моделирует прерывание commit и доказывает восстановление только разрешённых managed/manifest/VMF targets, включая отказ от подменённого journal. Дополнительные coordinator cases в `test_generation_pipeline.py` проходят полный synthetic compile-run и no-op через production runtime.

Warm-cache matrix в `test_generation_pipeline.py` повторяет тот же compile-run без Crowbar/StudioMDL, затем отдельно повреждает один model companion, удаляет один colored VMT и изменяет исходный MDL. Проверяется минимальный repair batch, generated/reused statistics и обязательный abort, если reused-файл изменился между planning и commit.

`test_cli.py` фиксирует основные и deprecated аргументы, поиск отдельного Crowbar и no-op CLI. `test_frozen_executable.py` по явному `PSR_FROZEN_EXE=<path>` запускает настоящий one-file exe в полностью временном проекте без Python/Crowbar/StudioMDL, проверяет `vmf_out`, LocalAppData manifest, deprecated warning и жёсткий предел 64 MiB; по умолчанию этот тест пропускается.

`test_external_sdk_generation.py` по явному `PSR_RUN_EXTERNAL_SDK=1` запускает read-only/isolated matrix на реальном `book_2`, Crowbar 0.68 и StudioMDL SDK 2013 SP. Direct `insert`, direct `replace` и source-Patch full-copy cases используют только temporary staging/overlays; capacity cases доказывают успешные 31 material/1024 family rows и отказ StudioMDL на 32 material/1025 rows. Default test run пропускает их; протокол границ находится в `docs/research/SDK_SKIN_LIMITS.md`.

`test_external_runtime.py` по явному `PSR_RUN_EXTERNAL_RUNTIME=1` выполняет полный production cold-run на временной копии `aa_models_color_tint_test_01a.vmf`, структурно проверяет итоговый VMF и manifest, а затем повторяет запуск без Crowbar/StudioMDL и доказывает побайтово стабильный warm-cache reuse. Исходный VMF и managed trees Antenna сверяются до/после. Протокол находится в `docs/research/PRODUCTION_RUNTIME_VALIDATION.md`.

`test_skin_layout.py` дополнительно проверяет project-wide reconciliation: добавление нового colored row к стабильному layout возвращает в generation plan закэшированные масштабы других карт, чтобы один `_scaled_XXX` path не смешивал разные skin-layout revisions.
