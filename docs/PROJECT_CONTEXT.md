# Память проекта props_scaling_recompiler

Дата фиксации: 2026-08-30.

## Назначение

`props_scaling_recompiler` (PSR) — compile-time инструмент для Hammer++, который заменяет `prop_static_scalable` на обычный `prop_static` либо аварийный `prop_dynamic_override` fallback и автоматически создаёт необходимые варианты Source-моделей. Основные задачи 2.0:

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
- Добавлен production-адаптер `psr.assets.mdl`: он разрешает исходный MDL через ordered SearchPaths, читает MDL 44–49 посредством `srctools.mdl.Model` и возвращает immutable `SourceAssetMetadata` с provenance, SHA-256 MDL/companions, static flag, bone count, `$cdmaterials`, полной skin-reference table, отдельными mesh-used material slots и точными ссылками на реально используемые VMT. `models/psr_scaled/**` отклоняется до чтения как managed output.
- Добавлены детерминированные synthetic MDL v44/v48 fixtures и contract-тесты для folder/VPK, static/dynamic, нескольких материалов и skins, отсутствующего VMT и повреждённого offset. Бинарники fixtures строятся во временном каталоге и не коммитятся.
- Добавлен байтовый source-preserving VMF parser `psr.keyvalues.vmf`: он сохраняет исходные bytes и spans, порядок и повторяющиеся свойства/блоки, различает direct и nested properties, понимает комментарии и не выполняет сериализацию при discovery.
- Добавлен read-only pipeline `discover_vmf_requests -> inspect_map_sources -> build_operation_plan`. Он игнорирует entities внутри top-level `hidden`, связывает активные VMF requests с `SourceAssetMetadata`, агрегирует generated-model requirements независимо от color/skin и color requirements независимо от scale, но ничего не генерирует и не изменяет VMF.
- Добавлен pure resolver `psr.domain.resolve_compile_scale`, совпадающий со всеми 35 подтверждёнными Hammer++ cases. Он читает беззнаковый десятичный prefix, использует fallback `1.0`, применяет PSR clamp `0.01` и decimal `ROUND_HALF_UP` до сотых; prefix/fallback/clamp/rounding становятся явными warnings `OperationPlan`. `scaled_model_path()` детерминированно назначает managed `_scaled_XXX` path из целого процента, форматируя его с минимальной шириной 3.
- Добавлен model-aware `psr.domain.resolve_geometry_scale`: для one-bone non-static MDL он возвращает `compile_scale²`, для static или многокостной модели — линейный compile scale. Geometry ниже `0.01` получает отдельный product clamp и диагностику. Compile identity и имя `_scaled_XXX` остаются линейными.
- Добавлен production-адаптер `psr.assets.vmt` поверх `srctools.vmt.Material`: он разрешает исходный VMT через ordered SearchPaths, раскрывает Patch, нормализует shader/parameters/blocks/proxies, сохраняет provenance и SHA-256 каждого VMT в dependency graph и вычисляет единый dependency fingerprint. Managed input `materials/models/psr_scaled/**` отклоняется до чтения.
- Добавлена отдельная read-only material phase `inspect_colored_material_sources -> build_colored_material_plan`. Она дедуплицирует identity по полному source VMT path и canonical RGB, назначает managed VMT paths, выбирает существующий `$color`/`$color2` либо `$color2` для утверждённых `VertexLitGeneric`/`UnlitGeneric`. Префикс shader name `SDK_` при выборе policy трактуется как alias базового имени (`SDK_VertexLitGeneric -> VertexLitGeneric`, `SDK_UnlitGeneric -> UnlitGeneric`), но исходное shader name в VMT не переписывается. Неподдерживаемый базовый shader остаётся явной ошибкой и с `SDK_`. Прямой VMT планируется как generated Patch; исходный Patch консервативно планируется как `full_copy` до SDK-проверки Patch-chain. Итоговый skin index намеренно остаётся следующему cache-backed skin-layout этапу.
- Добавлена synthetic VMT matrix для отсутствующего/existing color-key, proxies, Patch, unsupported shader и folder/VPK provenance. Material phase ничего не записывает и не запускает Crowbar/studiomdl.
- Добавлен project-scoped JSON manifest schema v1 в `psr.cache`: нормализованный абсолютный `GameInfo.txt` задаёт стабильный `project_id`, текущий SHA-256 GameInfo хранится отдельно, а SourceAsset/GeneratedModel/ColoredMaterial/SkinMapping/MapUsage являются раздельными строго валидируемыми таблицами. Поддержаны migration v0→v1, безопасный cold start/recovery повреждённого или несовместимого cache и атомарная запись temp+`os.replace`; путь хранения пока передаётся явно до CLI-интеграции.
- Добавлен pure cache-backed `build_skin_layout_plan`: исходные skin indices сохраняются, валидные ранее назначенные colored mappings удерживают индексы, новые `(source skin, RGB)` сортируются и дописываются после них. Полный layout получает fingerprint; entity получает отдельный final skin assignment. `commit_skin_layout_plan` только строит manifest-кандидат и не пишет его: вызывать его разрешено после будущей проверки generated artifacts. При изменении исходной skin table старые mappings и связанные `MapUsage` других карт для модели инвалидируются.
- Добавлен собственный байтовый `psr.assets.qc`: token-aware lexer валидирует quotes/comments/braces, различает top-level и вложенные команды и применяет только span-edits, не пересериализуя остальной QC. Reference transform сверяет исходный `$texturegroup "skinfamilies"` с таблицей MDL, добавляет `$staticprop` для dynamic source, удаляет только top-level `$definebone` при dynamic-to-static conversion и устанавливает полный стабильный layout. Variant transform назначает managed `$modelname`, записывает отдельный geometry scale в `$scale`, умножает на него только top-level `$lod` distances и parser-aware удаляет top-level `$bbox`/`$cbox`/`$illumposition`; compile identity остаётся в имени и плане, collision и остальные команды остаются побайтно нетронутыми до compile-validation.
- Добавлен pure `build_qc_operation_plan`: один reference QC переиспользуется всеми scale variants исходной модели; каждый in-memory artifact имеет source/output SHA-256, список mutations и детерминированный staging-relative path. Отсутствующий QC, рассогласование static flag MDL/QC и stale skin table становятся diagnostics. Этап ничего не пишет и не запускает Crowbar/studiomdl.
- Добавлен изолированный `StagingWorkspace`: каждый operation получает уникальный marked root под явно заданным parent, пути не могут выйти за его границы, повторная запись с другим content запрещена, source MDL/companions перед materialization повторно сверяются по provenance, size и SHA-256. Cleanup удаляет только собственный marker-protected root; staging можно явно сохранить для диагностики.
- Добавлены argv-only adapters Crowbar и StudioMDL без `shell=True`, с timeout, byte-preserving stdout/stderr и категоризированными ошибками запуска/exit code. Crowbar считается успешным только при одном однозначном QC в предварительно пустом output directory. StudioMDL отделён от post-compile validation: generated model проверяется по managed path, непустому полному companion set, MDL version/internal name и static-prop flag, а не по наличию строки `Completed` в логе.
- Добавлен детерминированный generator цветных VMT `psr.assets.generate_colored_material`: planned direct VMT становится generated Patch, planned source Patch — раскрытой full-copy с effective shader/parameters/blocks/proxies; выбранный `$color`/`$color2` получает канонический integer RGB vector. Каждый output повторно парсится через `srctools.vmt.Material` и получает SHA-256. Это подтверждает структуру и semantic round-trip generated текста, но не заменяет отдельную runtime-проверку Patch `insert`/`replace` на SDK 2013 SP.
- Добавлен staged coordinator `psr.pipeline.generate_and_validate`. Он принимает только валидные operation/material/skin plans, повторно проверяет VMT dependency graph и source MDL provenance/hash, генерирует VMT в staging game root, декомпилирует каждый уникальный source model один раз, строит reference/variant QC, размещает compile-ready variant рядом с Crowbar QC для сохранения относительных путей, запускает StudioMDL и валидирует каждый обязательный companion. Возвращаемый immutable `GenerationResult` ничего не публикует в реальный проект, manifest или VMF.
- Добавлен synthetic subprocess end-to-end contract: два scale variants и два цветных материала проходят через одну fake-Crowbar декомпиляцию и две fake-StudioMDL компиляции; отдельный dynamic scale 1.0 получает static `_scaled_100`; no-op не запускает tools; изменение VMT после planning и отсутствие `.sw.vtx` останавливают операцию, после чего marker-protected staging удаляется, а внешний sentinel остаётся неизменным.
- Реальная opt-in SDK VMT matrix на `book_2` подтвердила staged coordinator и compile-совместимость direct Patch `insert`, direct Patch `replace` и source-Patch full-copy: все три cases получили static managed MDL и полный набор из шести companions без `KeyValues Error`. Первая попытка выявила и исправила потерю unused MDL skin slots: production metadata теперь сохраняет полную QC-compatible таблицу 8×8 отдельно от единственного mesh-used slot, поэтому colored row создаёт один VMT и сохраняет остальные семь исходных значений. Протокол — `docs/research/SDK_VMT_GENERATION_VALIDATION.md`; визуальная runtime-семантика оттенка остаётся отдельной границей.
- Добавлен pure `reconcile_generation_requirements`: если source fingerprint или skin-layout fingerprint модели изменился, map-local operation расширяется всеми совместимыми закэшированными scale identities. Это гарантирует, что общий физический `_scaled_XXX` path не останется с layout revision другой карты. Особый переход при увеличении числа оригинальных skin families восстанавливает прежнее число по началу contiguous-блока managed mappings, вставляет новые оригинальные rows перед ним, сдвигает все старые colored targets на разницу и обязательно перекомпилирует все закэшированные масштабы. `MapUsage` остальных карт при этом инвалидируется: до их повторной компиляции визуальный сдвиг в уже собранных уровнях считается ожидаемым следствием изменения исходного ассета. Изменение таблицы без доказуемого увеличения остаётся консервативным полным reset без переноса старых scales.
- Проверены пределы skin layout на целевом SDK. Публичный `studio.h` задаёт 32-entry `MAXSTUDIOSKINS` для texture/material table, но установленный StudioMDL отвергает уже 32-е уникальное имя с `Too many materials used, max 32`; безопасный compile-limit равен 31 уникальному материалу. Отдельный isolated probe принимает 1024 skin-family rows, а на 1025 завершает процесс с `EXCEPTION_ACCESS_VIOLATION`. Planning теперь не добавляет colored mapping, который превысил бы любой предел: выдаёт warning, оставляет entity на исходном skin и не генерирует отклонённые VMT. MDL/QC adapters дополнительно отклоняют уже переполненные входы. Протокол — `docs/research/SDK_SKIN_LIMITS.md`.
- Synthetic compile-validation matrix из 12 комбинаций перекрёстно покрывает static/dynamic source, отсутствие collision/`$collisionmodel`/`$collisionjoints` и отсутствие/наличие исходного `$scale`. Исторически она подтвердила, что transformer добавляет `$staticprop` только при необходимости, заменяет исходный `$scale` итоговым geometry scale, сохраняет collision и способен компилировать QC с побайтно сохранёнными explicit bounds, а также масштабирует только top-level LOD distance. Успешная компиляция не доказала корректный viewport culling; после полевого feedback сохранение explicit bounds заменено их parser-aware удалением.
- Реальная isolated SDK 2013 SP matrix успешно декомпилировала Crowbar 0.68 и скомпилировала StudioMDL четыре staged cases при compile scale 1.50: static `apt/fsmit01` с `$collisionmodel` и geometry 1.50; one-bone dynamic `apt/monitor01` и `props_se/doll01` с `$collisionmodel` и geometry 2.25; one-bone dynamic `props_vehicles/car_van1a_doors1a` с `$collisionjoints`, исходным `$scale 1` и geometry 2.25. Во всех случаях exit code равен 0, generated MDL имеет точный managed internal name и static flag, выпущены непустые `.mdl`, `.vvd`, `.dx80.vtx`, `.dx90.vtx`, `.sw.vtx` и `.phy`. Внешние Antenna/SDK assets не изменялись; компиляция выполнялась в отдельный staging game root. Протокол и границы результата зафиксированы в `docs/research/SDK_QC_COMPILE_VALIDATION.md`.
- Добавлен source-preserving `build_vmf_output`: он принимает только неизменившийся по SHA-256 исходный VMF и валидные operation/skin plans, повторно связывает top-level entity по ID и discovery span, меняет direct `classname/model/skin`, сохраняет остальные bytes и после span-edits повторно парсит документ и доказывает итоговые значения каждой затронутой сущности. Для `prop_static` удаляются direct `modelscale`, `rendercolor` и legacy `convert_prop_to_static`; VMF-only dynamic fallback сохраняет исходные `model`, `modelscale`, `rendercolor`, `skin` и удаляет только legacy/service keys. Hidden/nested blocks не затрагиваются; no-op output побайтно равен input.
- Добавлен финальный `build_commit_plan -> apply_commit_plan`: до записи он повторно хэширует каждый staged VMT/MDL companion, требует точного совпадения generation set и полноты reconciliation всех cached scale variants, строит строго валидный manifest-кандидат и VMF-кандидат. Публикация сначала создаёт и проверяет sibling temp-файлы, затем заменяет managed assets, manifest и последним `vmf_out`; при любой ошибке уже заменённые targets откатываются из уникальных backup-файлов. Durable recovery journal позволяет следующему запуску безопасно откатить прерванный процессом commit; journal принимает только точные managed roots, manifest и заданный `vmf_out`.
- Добавлен production runtime coordinator и CLI entry point `psr_entrypoint.py`: полный запуск связывает discovery, planning, reconciliation, generation, validation и commit, а no-op создаёт эквивалентный `vmf_out` без Crowbar/StudioMDL. Постоянный project state под `%LOCALAPPDATA%\PropsScalingRecompiler\projects\<project_id>` содержит manifest, lock, recovery journal и logs; короткий временный staging находится в соседнем managed `work`. OS-level project lock запрещает два одновременных compile-run одного проекта и автоматически освобождается после завершения процесса.
- CLI сохраняет обязательные `-game`, `-vmf_in`, `-vmf_out`; старые `-subfolders`, `-force_recompile`, `-check_origs`, `-remove_unused` и `-debug` принимаются как deprecated compatibility arguments, игнорируются и дают дедуплицированное предупреждение. Явный `-debug_cleanup 0/1/2` до основной работы позволяет: ничего не очищать; удалить managed assets и cache текущего проекта; либо дополнительно очистить cache/temporary state всех проектов. Cleanup выполняется под project locks и не затрагивает оригинальные assets или logs. Обычный запуск всегда завершается общим отчётом и корректным exit code.
- Добавлена воспроизводимая one-file PyInstaller-сборка для Windows 10/11 x64. Собственный hook исключает неиспользуемую FGD-базу и неиспользуемые runtime-ветви `multiprocessing`, `asyncio`, `srctools.run` и `srctools.steam`; `ctypes` сохраняется для включения ANSI-цветов в Windows-консоли. Frozen no-op regression проходит без установленного Python и model tools. Текущая сборка `2.0.0.dev4` с иконкой `props_scaling_recompiler_icon_v3.ico` имеет размер 10 666 600 байт (10,17 MiB), SHA-256 `90B3D278075222E0B43093B2973A55936C44A982E505CF061622C24B27CEF797`, ниже целевого бюджета 16 MiB и жёсткого предела 64 MiB; тот же hash подтверждён после установки в SDK `bin`. Тестовый комплект содержит только основной exe и отдельный `third-party/CrowbarCommandLineDecomp.exe`; VPKEdit в комплект не входит.
- Полный production runtime проверен на изолированной копии приоритетной `aa_models_color_tint_test_01a.vmf`: cold-run реальными Crowbar 0.68/StudioMDL обработал 27 entity, сгенерировал 17 моделей и 8 материалов, опубликовал 108 файлов за 7.522 с; tool-less warm-run переиспользовал все артефакты за 0.762 с с идентичными VMF и manifest. Тест выявил legacy path limit Crowbar: временный staging сокращён до `%LOCALAPPDATA%\PropsScalingRecompiler\work\<project_prefix>`, постоянная full-SHA project identity не менялась. Runtime автоматически создаёт staging GameInfo с writable root первым и concrete folder/VPK mounts в исходном порядке. Antenna, SDK и настройки Crowbar после протокола остались неизменными.
- Добавлен fail-closed warm-cache planner `plan_artifact_reuse`. Generated model переиспользуется только при совпадении project manifest, текущего source fingerprint, compile identity, static-conversion state, skin-layout fingerprint, полного канонического companion set, MDL internal name/static flag и общего artifact fingerprint. Colored VMT требует совпадения полного source VMT dependency fingerprint, RGB, режима/параметра/пути генерации и физического SHA-256. Cache misses образуют минимальный generation batch: повреждение одного model variant перекомпилирует только его, отсутствие одного VMT регенерирует только материал, а изменение source MDL инвалидирует все его cached scales. Все reused-файлы повторно сверяются при построении commit plan и непосредственно перед транзакцией; VMF никогда не получает неподтверждённую ссылку. CLI отдельно сообщает generated/reused counts.
- Добавлен fail-soft outcome ledger с минимальными dependency closures для entity, color/skin usage, shared VMT, scale variation и whole source model. Material generation, source decompile/reference QC и StudioMDL variants собирают независимые результаты; runtime перестраивает operation/material/skin plans после отказов и публикует только доказанные artifacts и `MapUsage`. Synthetic end-to-end regression подтверждает, что отказ `_scaled_150` не блокирует независимый `_scaled_200` той же original model.
- Общий аварийный fallback реализован как source-preserving VMF assignment `prop_dynamic_override`: исходные `model`, `modelscale`, `rendercolor` и `skin` сохраняются побайтно, удаляется только service key. CLI-флаг `-dynamic_fallback 0/1` по умолчанию равен 1; при 0 failed entity остаётся исходным `prop_static_scalable`. Особый подтверждённый empty-bodygroup путь остаётся `prop_dynamic`, но отсутствие static-result показывается красным `ERROR`.
- Валидный partial или byte-equivalent passthrough `vmf_out` теперь является единственным критерием exit code 0. Ошибка commit после rollback и восстанавливаемая ранняя ошибка доставляют структурно проверенный исходный VMF атомарно; exit code 1 остаётся только невозможности передать валидный output. Итоговый отчёт содержит ровно `ERROR`/`WARNING`/`INFO`, группирует одинаковые причины и entity IDs, а severity не определяет exit code.
- Введён централизованный console progress reporter: каждая крупная фаза получает короткое стартовое сообщение, завершение каждой material/model порции сразу показывает `completed/total`, процент, текущий logical path, elapsed и расчётный ETA, а пятисекундный heartbeat остаётся страховкой внутри одной долгой внешней операции. Чтение входного и транзакционная запись выходного VMF дополнительно показывают порционный byte-progress; завершение batch явно показывает 100%. CLI выводит banner/контакты, объясняет правильное размещение EXE/tools и завершает INFO-блок в порядке project cache summary (вариации, maps/usages, число и размер managed-файлов) → session summary → elapsed time.
- Out-of-range decimal `skin` нормализуется в effective 0 с warning, сохраняя raw VMF value; colored identity строится от skin 0. QC bounds removal включён в версионированный model-generation recipe внутри layout fingerprint, поэтому прежние MDL инвалидируются. Для каждого реально окрашиваемого effective VMT без `"$blendtintbybasealpha" "1"` добавляется дедуплицированный warning.
- Исследовательские скрипты `is_staticprop.py`, `skins_from_mdl.py` и `mdl_skins_and_cdmaterials*.py` подтверждают возможность чтения static flag, material table, `$cdmaterials` и skin families непосредственно из MDL.
- Пользовательское незакоммиченное изменение в `props_scaling_recompiler.py`: версия `2.0.0 - dev 001` заменена на `2.0.0 - dev 002`.

## Целевая среда 2.0

Первый релиз PSR 2.0 поддерживает только:

- Windows 10/11 x64;
- Source SDK 2013 Singleplayer;
- Hammer++;
- MDL и toolchain фактической SDK 2013 SP среды пользователя.

Garry's Mod, Portal 2 и другие ветки Source не входят в scope первого релиза 2.0.

Локальное референсное окружение пользователя:

- SDK bin: `C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin`;
- реальный мод: `C:\Program Files (x86)\Steam\steamapps\sourcemods\antenna_sdk2013`;
- production-комплект 1.1.2: `prod/props_scaling_recompiler_v1.1.2`.

Зафиксированные внешние инструменты production pipeline:

| Инструмент | Версия | SHA-256 |
|---|---:|---|
| CrowbarCommandLineDecomp.exe | 0.68.0.0 | `4B5FC8F5092448C1F8FE12F6849BF8EE3996406F02109EC90AB800C6CF145B2A` |
| studiomdl.exe из SDK 2013 SP | file version отсутствует | `E6C4EA7477B8CE31DE878FF53CA640CB222C4978F3BA33C4715DE3DE1C7A6416` |

Crowbar из production-комплекта 1.1.2 побайтно совпадает с установленным в SDK `bin` и поставляется отдельным файлом рядом с PSR 2.0. Исторический `vpkeditcli.exe` 4.2.3 (`A28E5B596161995BEE529DF2FCB06F754482255B2306F697D4FEF4F6F79BEA2A`) больше не является зависимостью и не входит в комплект 2.0.

## Роль srctools 2.7.0

`srctools==2.7.0` принят как закреплённая runtime-зависимость PSR 2.0. Библиотека распространяется под MIT и предоставляет проверенные реализации для Valve KeyValues, виртуальных файловых систем, VPK, MDL и VMT. В проекте она используется через собственные тонкие adapters, чтобы продуктовые правила PSR не зависели от неявной политики высокоуровневых helpers.

Upstream и документация: `https://github.com/TeamSpen210/srctools`, `https://srctools.readthedocs.io/`.

Подтверждённые области повторного использования:

- `srctools.keyvalues` — semantic parse и структурная валидация GameInfo/VMT, а также дополнительная проверка VMF;
- `srctools.filesys.RawFileSystem`, `VPKFileSystem` и `FileSystemChain` — точный lookup полного logical path в папках и directory VPK;
- `srctools.mdl.Model` — MDL versions 44–49, static-prop flag, `$cdmaterials`, skin families и разрешение материалов;
- `srctools.vmt.Material` — shader/parameters/proxies, раскрытие Patch и сбор его include-зависимостей;
- собственный минимизирующий PyInstaller hook — для production Windows-сборки.

Ограничения интеграции:

- `srctools.vmf.VMF.export()` не является source-preserving writer: он нормализует форматирование, удаляет обычные комментарии, меняет порядок/представление части данных, схлопывает повторяющиеся direct entity keys и по умолчанию увеличивает map version. Итоговый VMF поэтому редактируется собственным lossless span-editor; `srctools.vmf` допустим как semantic reader/validator.
- `srctools.game.Game.get_filesystem()` нельзя использовать как resolver PSR. В версии 2.7.0 он группирует VPK раньше folder roots и автоматически добавляет некоторые DLC/update/platform paths, поэтому фактический порядок отличается от строк `SearchPaths`. PSR самостоятельно разбирает GameInfo и вручную собирает `FileSystemChain` строго в утверждённом порядке.
- `srctools.mdl.Model` 2.7.0 отбрасывает неиспользуемые material slots, проходя по Python `set`. Адаптер PSR повторно читает offsets таблиц texture/skin/bodypart/model/mesh, сохраняет полную numeric skin-reference table для QC round-trip, отдельно сортирует mesh-used slot indexes для VMT resolution/покраски и сверяет их проекцию с `srctools` без зависимости от set order.
- QC библиотекой не поддерживается; для него остаётся собственный token-aware transformer.
- Семантика VMT Patch из библиотеки полезна как parser/evaluator, но реальный выбор `$color`/`$color2` и поведение `insert`/`replace` всё равно проверяются на SDK 2013 SP.

Read-only probe на Antenna подтвердил применимость установленной версии и собственных ordered/MDL adapters: 35 исходных SearchPath leaves развёрнуты в 39 конкретных mounts без группировки VPK перед folder roots; `models/props_se/storage/book_2.mdl` разрешён через исходную строку `|gameinfo_path|.` из project folder. Production-адаптер определил MDL v48 как non-static, сохранил восемь skin families, разрешил первые четыре реально существующих VMT по `models/props_se/book/` и зафиксировал MDL, PHY, VVD и три VTX companions с размерами и SHA-256. Отсутствующие optional paths, numbered VPK chunks и отсутствующие VMT остаются явным состоянием/diagnostics, а не ломают discovery.

Read-only QC inventory разобрал без structural errors все 392 найденных `.qc` в Antenna и сохранённом SDK temp: 355 static и 37 dynamic scripts, 147 со `$scale`, 7 scripts/15 commands с `$lod`, 257 с `$collisionmodel`, 10 с `$collisionjoints` и 8 со `skinfamilies`. Реальный Crowbar 0.68 QC `fsmit01.qc` подтвердил важную QC-лексему `$cdmaterials "models\apt\"`: обратный слеш перед закрывающей кавычкой является path separator, а не KeyValues-style escape. Его skin row точно совпал с MDL `models/apt/fsmit01.mdl`; reference transform при неизменном layout вернул исходные bytes без mutations. Внешние файлы не изменялись, Crowbar и studiomdl не запускались.

`vpkeditcli.exe` исключён из production toolchain и поставки: folder/VPK resolution выполняет закреплённый `srctools==2.7.0` через собственную ordered SearchPaths policy PSR.

## Утверждённые продуктовые правила

### Приоритеты проекта

При споре требований используется следующий порядок: безопасность файлов пользователя и оригинальных ассетов > UX и рабочий процесс художника > функции PSR > минималистичность и простота архитектуры > производительность PSR > безопасность рабочих файлов PSR, включая generated assets, cache и `vmf_out` > экономия токенов > автотесты и самопроверки > общие правила хорошего тона > legacy/backward compatibility > всё остальное. Для 2.0 сознательно выбран практичный, менее строгий подход для небольшого круга пользователей; тесты остаются важны там, где непосредственно защищают более высокий продуктовый приоритет, но исчерпывающие перестраховочные матрицы не являются самостоятельной целью.

### Оригиналы

Оригинальные MDL, QC, VMT и companion-файлы никогда не изменяются. Все преобразования создают новые артефакты.

Оригинальная модель используется напрямую только при одновременном выполнении условий:

- она уже имеет static-prop flag;
- итоговый PSR compile scale равен 1.0;
- запрошенный цвет равен `255 255 255`.

Любая другая комбинация создаёт managed-модель PSR, включая обычный dynamic при scale 1.0 и покрашенный static при scale 1.0. Единственное исключение 2.0 — dynamic MDL с пустым option многовариантного bodygroup: он используется напрямую как `prop_dynamic` со всеми исходными runtime properties.

### Аварийный runtime fallback

По официальным исходникам Source SDK 2013 `prop_dynamic` и `prop_dynamic_override` связаны с одним классом `CDynamicProp`. Различие находится в `CDynamicProp::OverridePropdata()`: только classname `prop_dynamic_override` обходит удаление сущности из-за отсутствующего, повреждённого или несовместимого `prop_data`. Оба варианта наследуют `modelscale` и `skin` от `CBaseAnimating`, а `rendercolor` — от `CBaseEntity`. `prop_scalable` является отдельным простым `CPropScalable : CBaseAnimating`: он также наследует эти три свойства и без `prop_data`-проверок принимает любую загружаемую модель, но не создаёт VPhysics/collision, использует `MOVETYPE_NONE` и принудительно получает `EF_NOSHADOW`. Источники: [`props.cpp`](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/game/server/props.cpp), [`baseanimating.cpp`](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/game/server/baseanimating.cpp), [`baseentity.cpp`](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/game/server/baseentity.cpp), [`episodic/prop_scalable.cpp`](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/game/server/episodic/prop_scalable.cpp).

По совокупности поддержки scale/skin/color, терпимости к типу модели, collision и обычного dynamic-rendering фаворитом для общего аварийного fallback выбран `prop_dynamic_override`. Когда PSR не может корректно преобразовать конкретную entity, разрешённый fallback без собственных compatibility-проверок меняет её classname на `prop_dynamic_override`, оставляет исходные `model`, `modelscale`, `skin`, `rendercolor` и прочие runtime properties, удаляет только служебные PSR keys и выдаёт красный `ERROR` в итоговом отчёте. Проверки допустимости модели делегируются Valve tools/runtime. Конвертация включена по умолчанию и должна отключаться отдельным CLI-флагом; точное имя флага ещё не выбрано.

Уже существующий empty-bodygroup fallback пока остаётся отдельным подтверждённым частным путём с `prop_dynamic`; его унификацию с общим `prop_dynamic_override` нужно выполнить осознанно при реализации новой error policy, не переписывая историю полевого runtime-теста.

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

Корректно распарсенный десятичный `skin`, индекс которого не существует в текущей source skin-family table, повторяет подтверждённое поведение Hammer++ и игрового runtime: effective source skin равен 0. Downstream planning, покраска, skin-layout identity и итоговый VMF работают так, будто художник намеренно указал skin 0, но итоговый отчёт обязательно содержит жёлтый warning с raw/effective index, моделью и затронутыми entity IDs. Исходная raw-строка остаётся в `VmfEntityRequest` до нормализации и повторно читается из исходного VMF при следующем compile-run. Если original MDL изменится и прежний raw index станет существующим, новый source/skin-layout fingerprint инвалидирует все cached scale variants этой модели, после чего request интерпретируется уже как новый настоящий skin и все variations перекомпилируются. Недесятичная строка `skin` не является «несуществующим индексом» и пока остаётся отдельным malformed request.

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

Реализованный pre-generation `OperationPlan` всегда устанавливает `requires_vmf_output=True`, включая no-op карту. Он хранит raw `modelscale` в `VmfEntityRequest`, получает итоговый `Decimal` compile scale, выводит отдельный model-dependent geometry scale и назначает детерминированный output model path. План определяет `reuse_original` для static + compile scale 1.0 + white, `reuse_dynamic` для утверждённого empty-bodygroup fallback и `generate_model` для остальных usages. `reuse_dynamic` указывает на исходный MDL и исключается из model/VMT/skin-layout/reconciliation batches. Model requirements агрегируются уже по округлённому compile scale, поэтому `1.095`, `1.1` и `1.104` переиспользуют одну `_scaled_110`; geometry остаётся однозначной благодаря source fingerprint с MDL metadata.

Нельзя записывать VMF, указывающий на неподтверждённый generated artifact. При этом pipeline является fail-soft: корректная часть работы коммитится, повреждённые requests/variations пропускаются или получают утверждённый runtime fallback, а художник получает единый отчёт.

### Политика ошибок и частичного успеха

Утверждённая единица отказа — наименьшее замкнутое множество реально зависимых результатов, а не вся карта и не безусловно вся original model:

- malformed request одной entity отклоняет только эту entity;
- ошибка конкретной комбинации color/material/skin отклоняет только зависящие от неё usages;
- ошибка компиляции одного масштаба отклоняет конкретную variation `(original model, compile scale)` и использующие её entities;
- невозможность разрешить/decompile original model, построить общий reference QC или получить согласованный общий skin layout отклоняет original model и все её variations;
- ошибка shared VMT отклоняет все и только те variations/entities, которые от него зависят;
- layout revision/reconciliation, при котором невозможно согласованно обновить хотя бы один cached scale, отклоняет original model целиком;
- ошибка чтения исходного VMF, структурной записи/валидации `vmf_out` или иная ситуация, когда корректный `vmf_out` нельзя передать дальше, завершает весь run с exit code 1.

Если корректный partial либо byte-equivalent passthrough `vmf_out` создан и валидирован, run всегда возвращает exit code 0, даже когда отчёт содержит красные errors, пропущенные entities/models или fallback-замены. Exit code 1 зарезервирован исключительно для случаев, когда PSR не смог передать корректный `vmf_out` следующему compile step. В самом плохом восстанавливаемом случае PSR пишет эквивалент оригинального `vmf_in`, не меняя его содержимое; неизвестный движку `prop_static_scalable` в таком output допустим.

Каждый compile-run, включая no-op и завершение с ошибкой, заканчивается единым сводным отчётом. Пользователь не должен искать диагностику по промежуточному логу. Итоговый exit code вычисляется независимо от severity после печати отчёта.

Отчёт имеет ровно три дедуплицированные группы:

- красный `ERROR`: PSR не смог создать или предоставить требуемый static-result. Успешный перевод entity в `prop_dynamic_override`, сохранение исходного `prop_static_scalable` или другой fail-soft fallback не понижает такой случай до warning; fallback лишь позволяет продолжить compile-run и получить exit code 0 при валидном `vmf_out`. Уже готовая подходящая original static model считается успешно предоставленным static-result и ошибкой не является;
- жёлтый `WARNING`: static-result успешно получен и может быть использован, но request/result был нормализован, ограничен, угадан или имеет известный дефект/нюанс. Сюда относятся skin/material capacity fallback, Hammer-необычный scale (`0.001`, `1,0`, `blablabla`), известная проблема collision transform при пригодной статической модели, out-of-range skin с effective 0 и будущий поиск original model по указанной generated variation;
- нейтральный `INFO`: статистика проекта и текущей сессии — discovered/processed/generated/reused/skipped/fallback counts, размеры, elapsed time, cache/project summary и необязательные короткие весёлые факты.

Если одна первопричина порождает несколько сообщений, итоговый отчёт оставляет наивысшую severity и объединяет модели/вариации/entity IDs, не спамя одинаковыми строками. Практическую рекомендацию по исправлению следует прикладывать к соответствующей `ERROR`/`WARNING` записи, а не выделять четвёртую категорию. При отсутствии ANSI/console color смысл сохраняют текстовые заголовки и метки.

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

Для Source SDK 2013 SP действуют два независимых предела: не более 31 уникального material name в компилируемом QC и не более 1024 rows в `skinfamilies`. Перед добавлением каждого нового colored mapping planner считает оба будущих значения. Если хотя бы один предел превышается, mapping и его VMT не входят в operation batch, создаётся warning с моделью/source skin/RGB/entity ids, а VMF assignment использует исходный skin. Это fail-soft поведение не отменяет остальные варианты той же модели.

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
- Top-level `$lod` distance умножается на geometry scale. `$shadowlod`, collision blocks, sequences и прочие команды не изменяются неявными числовыми regex-заменами. Top-level explicit bounds `$bbox`, `$cbox` и `$illumposition` удаляются parser-aware, чтобы StudioMDL пересчитал их по итоговой модели; это возвращает поведение 1.1.2 и устраняет преждевременное исчезновение крупных scaled props у границ экрана. Если полевой тест выявит некорректный автопересчёт StudioMDL, собственный расчёт bounds остаётся будущей задачей. Для исходно static QC `$definebone` сохраняется. При dynamic-to-static conversion только top-level `$definebone` удаляется parser-aware: полевой regression `psr_test_01a` показал, что иначе StudioMDL сохраняет неиспользуемые исходные bones перед vertex-used `static_prop`, тогда как static-prop runtime применяет entity transform только к bone 0; render mesh оказывается у world origin, хотя PHY остаётся у entity.
- Квадратичный geometry scale утверждён только для исходного MDL с одной костью без static-prop flag; прежняя эвристика по `prop_data` отвергнута. Post-compile validation требует ровно одну кость у каждого dynamic-to-static результата, поэтому прежние многокостные broken artifacts отклоняются и не могут попасть в VMF или warm reuse.

## Cleanup и миграция

Нормальный cleanup 2.0 работает только внутри:

```text
models/psr_scaled/
materials/models/psr_scaled/
```

Эти каталоги считаются managed-пространством PSR. Агрессивная актуализация допустима только после построения полного плана, проверки cache/manifest и успешного разрешения источников.

Удаление больше не используемых colored mappings и уплотнение их индексов не выполняется обычным compile-run: частая смена цвета на одной карте не должна постоянно сдвигать layout остальных карт. В будущем это обязательная часть явного cleanup-режима (с dry-run): он строит project-wide usage set по manifest и известным картам, находит mappings без единого `MapUsage`, предлагает их удаление только когда требуется освободить capacity либо пользователь явно запросил compaction, вычисляет новую непрерывную таблицу и список всех затронутых карт/масштабов. До commit cleanup обязан предупредить, что эти карты нужно перекомпилировать; все managed scale variants модели пересобираются одной layout revision. Без полного project-wide доказательства отсутствия ссылок colored row не удаляется.

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

Read-only проверка VMF discovery подтверждает текущие counts трёх приоритетных карт: 27/118/62 активных PSR entities, ноль hidden и ноль structural diagnostics. Для `aa_models_color_tint_test_01a.vmf` все восемь уникальных моделей дополнительно разрешены и прочитаны через production MDL adapter без diagnostics. После подключения scale resolver полный pre-generation plan этой карты также валиден: 27 usages агрегированы в 17 generated-model requirements и 8 color/skin requirements без diagnostics.

Read-only material inventory той же `aa_models_color_tint_test_01a.vmf` при неизменном SHA-256 `18AEDE35A65477A3CECD00B6E063DE3E5807F5FB7388DD77C37F80958F57B69D` нашёл четыре уникальных реально требуемых VMT и восемь `(source VMT, RGB)` generated identities. Все четыре VMT разрешены из folder SearchPath, используют прямой `VertexLitGeneric`, не содержат `$color`/`$color2`, proxies или Patch dependencies. Текущий консервативный план для всех восьми identities валиден и выбирает `$color2` + `insert` + `patch`; внешние файлы не изменялись.

Cold-cache skin-layout inventory этой карты построил восемь model layouts и 27 final entity assignments без ошибок. Восемь colored mappings распределены между `airplane_funal_parachute` (исходный skin 0, цветной index 1) и `book_2` (восемь исходных rows 0–7, семь цветных rows 8–14). Для `book_2` mappings отсортированы по `(source skin, RGB)`, поэтому skins 0/1/2 и все запрошенные цвета получают воспроизводимые индексы; Antenna и manifest при inventory не изменялись.

Обновлённая `psr_scale_compatibility_01a.vmf` структурно валидна: 51 active request, 63 081 байт, SHA-256 `bb6766854efb6584a7a8bd37e64e24212490c702082eb2946b6b9790b20071ee`. Первые 35 `fsmit01` cases сохраняют parsing oracle; ещё 16 requests подтверждают линейную static/multi-bone и квадратичную one-bone non-static ветви на `fsmit01`, `monitor01`, `doll01` и `door02_double`. Исторический read-only plan до введения VMF-only door fallback разрешил шесть исходных MDL, построил 51 usage и 21 generated identity без ошибок; все 51 рассчитанных geometry scale точно совпали с `debug_string`. В текущем 2.0 плане usages `door02_double` относятся к `reuse_dynamic` и не создают generated identities; сама scale-матрица остаётся oracle исследованного Hammer++ viewport. 23 ожидаемых warnings относятся к parsing/fallback/clamp/rounding исходной строковой матрицы. Внешняя карта и ассеты не изменялись.

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
| `psr_test_01a.vmf` | `A468E5D3527FF3E7D9F271EEEC08AC6220BF100B4F8F923B43B0BC2F4711C58D` | 62 | 11 | 62 | 0 | 29 |

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

Компактная regression-карта для scale/static conversion, Hammer++ scale parsing, покраски и out-of-range skin normalization.

- Структурно валидный снимок от 2026-08-30 имеет 63 564 байта, 62 PSR entities, 11 моделей, 62 уникальных raw requests, ноль точных повторов, ноль hidden PSR entities и ноль structural diagnostics.
- 58 entities используют raw skin 0. `concrete_tile_256_2.mdl` имеет ровно две source families (валидные индексы 0/1): entity 1617/1651 проверяют валидный skin 1 при scale `1.0`/`0.60`, а entity 1621/1653 намеренно запрашивают несуществующий skin 2 при тех же масштабах и должны нормализоваться в effective skin 0 с одним дедуплицированным жёлтым warning, перечисляющим обе entity.
- 29 non-white entities охватывают тринадцать non-white RGB; вместе с white это четырнадцать цветовых значений.
- Raw scale matrix: `1.0` (18), `0.50` (17), `1.50` (15), `0.60` (3), `3` (2), `1` (2), а также по одной entity с `0.35`, `1,0`, `3,0`, `invalid_scale_test` и `blablabla`.
- Read-only WIP plan разрешает все 11 source MDL и строит 62 usages, 24 generated-model requirements и 17 colored-skin requirements. Текущий временный код ещё не создаёт утверждённый out-of-range skin warning и ошибочно считает три empty-bodygroup dynamic fallback предупреждениями. После исправления ожидаются три красных `ERROR` для entity 540/542/544, четыре Hammer scale `WARNING` и один дедуплицированный out-of-range skin `WARNING` для raw skin 2 с entity 1621/1653.
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
- расширение подтверждённой `$color`/`$color2`-матрицы за пределы базовых `VertexLitGeneric`/`UnlitGeneric` и материалов с уже существующим color-key; варианты с префиксом `SDK_` наследуют policy базового shader и отдельного разрешения не требуют;
- точная runtime-семантика generated Patch `insert`/`replace` и Patch-chain на SDK 2013 SP;
- способ fingerprint зависимостей для Patch из VPK;
- политика переноса legacy-generated модели, выбранной как новый original;
- полевой smoke общего fallback на `prop_dynamic_override`: сохранение `modelscale`/`skin`/`rendercolor`, видимая геометрия, collision и отсутствие удаления representative static/dynamic/physics моделей. Это проверка выбранного поведения, а не compatibility-gate перед каждой заменой;
- retention/cleanup policy для сохранённых staging и failed runs;
- UX и CLI-флаг явного project-wide colored-layout cleanup/compaction; базовая safety-policy и требование dry-run уже утверждены.
- будущий расширенный SMD-aware этап для полноценной static-конвертации моделей с пустыми bodygroup options, а также корректировки skeleton/bone transforms и поворотов collision; это отдельная задача после версии 2.0. Прежний staging-only zero-triangle bodygroup `blank` workaround оказался compile-safe, но runtime-опасным и удалён. В 2.0 редкие dynamic MDL с пустым bodygroup option намеренно остаются исходными `prop_dynamic` без декомпиляции/компиляции.

## План следующей итерации

1. Изолировать документационную память от пользовательского WIP и после явного подтверждения откатить только неудачные временные progress/skin-правки, сохранив утверждённое parser-aware удаление bounds и пользовательскую версию `dev 002`.
2. Зафиксировать маленькими contract fixtures fail-soft outcomes и dependency closures: entity-only, color/material usage, model+scale variation, whole original model, shared VMT и невозможность `vmf_out`. Отдельно проверить контракт exit code 0/1 и byte-equivalent passthrough.
3. Заменить all-or-nothing generation/commit на сбор результатов по единицам работы: продолжать независимые операции, валидировать и публиковать только успешные artifacts, обновлять manifest/`MapUsage` только доказанными результатами и передавать VMF writer полный outcome ledger.
4. Реализовать общий `prop_dynamic_override` fallback без model compatibility-gate, с сохранением runtime properties, красной итоговой диагностикой и CLI-переключателем default 1. При отключённом fallback неудачная entity остаётся исходным `prop_static_scalable`.
5. Сделать `vmf_out` главным критерием успеха run: partial/passthrough output валидируется структурно и возвращает 0; только невозможность передать валидный output возвращает 1. После этого собрать единый дедуплицированный трёхсекционный отчёт `ERROR`/`WARNING`/`INFO`, где severity не определяет exit code.
6. Завершить bounds-изменение: обновить старые tests, добавить version/fingerprint generation recipe для инвалидирования уже собранных MDL после изменения QC-transform semantics и полево проверить culling крупных моделей.
7. Добавить централизованный UX progress/heartbeat для этапов дольше пяти секунд, стартовый banner/контакты, понятные сообщения о неверном размещении EXE и итоговый elapsed time. Временные разрозненные `print()` не использовать как интерфейс.
8. Добавить regression для out-of-range integer skin: raw сохраняется на входе, effective skin становится 0 с дедуплицированным warning, colored request использует `(skin 0, RGB)`, а увеличение source skin-family count меняет effective skin и инвалидирует все scale variants модели. Отдельно провести smoke выбранного `prop_dynamic_override` на representative static/dynamic/physics models.
9. Локализовать дефект `book_2` отдельным QC/SMD/bone-transform fixture: сравнить render geometry, PHY/collision и root bone transform до/после decompile/compile; исправление не смешивать с общей fail-soft переработкой.
10. Добавить в итоговый отчёт предупреждение для окрашиваемого исходного материала без `"$blendtintbybasealpha" "1"`. После горящих задач расширить визуальную Patch matrix на replace существующего color-key, исходный Patch и proxies.

Статус 2026-08-30: кодовая и synthetic contract-часть пунктов 1–8 выполнена; warning из пункта 10 реализован. Не выполнялись внешний runtime smoke общего `prop_dynamic_override`, полевой culling-test крупных моделей и отдельная локализация `book_2` из пункта 9. Визуальное расширение Patch matrix также остаётся будущей полевой задачей.

## Полевой прогон 2026-08-22

- Настоящий frozen EXE установлен в SDK `bin` и проверен на трёх приоритетных Antenna-картах. `aa_models_color_tint_test_01a` и `psr_test_01a` полностью прошли cold/warm PSR и VBSP: суммарно 76 преобразованных сущностей, без missing MDL/VMT.
- Полевой VPK material выявил shader `SDK_VertexLitGeneric`. Утверждено общее правило: любой `SDK_<Shader>` наследует color-policy `<Shader>` без переписывания исходного shader name. Поэтому `SDK_VertexLitGeneric` и `SDK_UnlitGeneric` эквивалентны базовым вариантам для выбора `$color2`; неизвестный базовый shader остаётся fail-closed.
- Выявлен crash StudioMDL при dynamic-to-static QC с `blank` первым option в `$bodygroup`. Попытка сохранить option order/index посредством staging-only zero-triangle SMD позволила StudioMDL завершить compile, но полевой runtime-тест `door02_double` доказал `0xC0000005` в `shaderapidx9.dll`. Workaround и его API удалены. Утверждён общий MDL-детектор: dynamic source с bodypart `nummodels > 1`, содержащим option `nummeshes == 0`. Окончательный fallback 2.0 не компилирует такой asset вообще: output использует исходный MDL как `prop_dynamic`, сохраняет runtime `modelscale`/`rendercolor`/`skin` и не создаёт MDL/VMT/skin mappings.
- Первый игровой запуск `psr_test_01a` выявил неверную material binding: полное managed-имя `models/psr_scaled/...` внутри skin family складывалось StudioMDL с исходным `$cdmaterials`, и движок искал `models/<source-dir>/models/psr_scaled/...`. Утверждённый compile-контракт: QC получает отдельный `$cdmaterials "models/psr_scaled/"`, а managed texture names в skin families записываются относительно этого корня (`props_c17/...`, `apt/...`). Физический VMT по-прежнему находится под `materials/models/psr_scaled/...`. Версия binding включена в skin-layout fingerprint, поэтому старые MDL обязательно перекомпилируются без изменения стабильных skin indices.
- Все игровые вылеты до и после исправления material binding имеют одинаковый signature: `0xC0000005` в `shaderapidx9.dll+0xBA50D`, read address `0x1C`, `EAX=0`. Исправление путей устранило missing PSR materials, но не crash. Первоначальные 25 секунд без dump были недостаточным окном и проходили на VBSP-only/fullbright варианте; этот вывод отозван. Parser-aware деление fully compiled карты доказало: `r_drawstaticprops 0` стабилен 55 секунд, 24 colored props стабильны 42 секунды, 25 skin-0 props падают, 12 dynamic-to-static props падают, `monitor+door` падают, а три `monitor01` стабильны 42 секунды. Причина локализована до generated `door02_double_scaled_*` с zero-triangle bodygroup option.
- Промежуточный compiled-dynamic эксперимент настоящими Crowbar/StudioMDL собрал `door02_double_scaled_050/100/150` без static flag и сохранил исходный `blank`. Output с 46 `prop_static` и тремя `prop_dynamic` прошёл полный BSP и длительный runtime без прежнего crash signature. Окончательный контракт 2.0 упрощён до прямого reuse исходного dynamic MDL; экспериментальные managed door-файлы больше не являются требованиями pipeline. Во время полного запуска движок сам переключил video mode `2560×1440 -> 2048×1080`; обязательный `finally` восстановил registry `2560×1440`, fullscreen/no-border=0 и исходный SHA-256 `config.cfg`.
- Финальный frozen VMF-only fallback проверен на полном `psr_test_01a`: 49 сущностей, 0 generated/19 reused обычных моделей, 0 generated/22 reused материалов. Entity 540/542/544 используют исходный `door02_double.mdl` как `prop_dynamic`, побайтно сохраняют raw `modelscale` `0.50`/`1.0`/`1.50`, `rendercolor` и `skin`, не содержат `convert_prop_to_static`. Повторный run дал идентичный output SHA-256 `9D25CBC1EC4255B20A1BACD86866B69DB5194D8C86161CA888CF70F431187178`; тот же VMF прошёл `VBSP -> VVIS -> VRAD` без missing MDL/VMT. Игровой процесс в этой финальной проверке не запускался и video-настройки не затрагивались.
- Реальный StudioMDL обрезает `studiohdr_t::name[64]` до 63 ASCII-байт плюс NUL, хотя output-файл создаёт по полному `$modelname`. Post-compile validator строго сравнивает полный представимый префикс; неверное обрезанное имя остаётся ошибкой. Длинная generated model после этого успешно прошла VBSP по полному logical path.
- Оригинальная `aa_models_static_convert_test_01a` содержит 14 неразрешимых входных ссылок и потому корректно завершается fail-closed до generation. Структурная диагностическая копия без них выявила ещё два ограничения StudioMDL: PHY не создаётся для вырожденной collision холодильника при compile scale `0.01`, а сложная Hunter ragdoll/flex model падает при static conversion с `EXCEPTION_ACCESS_VIOLATION`.
- Поддерживаемая структурная выборка static-convert карты прошла 102/102 сущности, real VBSP и полный warm reuse: cold 37 generated + 22 reused models, warm 59 reused models, 0 публикаций. Полный протокол и открытые решения находятся в `docs/research/FIELD_RUNTIME_VALIDATION.md`.
- Старый VBSP может не открыть VMF с очень длинным промежуточным basename из-за собственного path buffer. Тот же VMF под коротким именем компилируется; compile configuration должна использовать короткие имена в `psr_temp`.

## Принятые решения

- Оригиналы неизменяемы.
- Generated-модели живут в `models/psr_scaled`.
- Generated-материалы живут в `materials/models/psr_scaled`.
- Используется только `_scaled_XXX`; `_static` не создаётся и не трактуется специально.
- Нейтральный static 1.0 использует оригинал; остальные случаи создают managed-модель.
- Effective scale повторяет видимое поведение Hammer++, включая его обработку необычных raw-строк.
- PSR compile scale получается из effective scale нижним clamp `0.01` и decimal `ROUND_HALF_UP` до сотых; generated identity и `_scaled_XXX` используют этот compile scale и точный целый процент с минимальной шириной 3 в имени.
- Geometry scale равен `compile_scale²` только для one-bone non-static source MDL и равен compile scale для static/multi-bone source; `prop_data` и PHY не переключают режим. Geometry ниже `0.01` клампится отдельно. Geometry присутствует в transient plan/QC artifacts, но не заменяет compile identity в кэше.
- Из итогового `prop_static` удаляются raw `modelscale`, `rendercolor` и служебные PSR properties; `skin` сохраняется как итоговый mapped index. Dynamic fallback 2.0 сохраняет исходные `model`, `modelscale`, `rendercolor` и `skin`, удаляя только `convert_prop_to_static` и будущие нерелевантные service keys.
- Общий аварийный fallback использует `prop_dynamic_override`, выполняется без PSR compatibility-проверок, сохраняет runtime properties и диагностируется как красный `ERROR`. Отдельный CLI-флаг управляет им, default равен 1.
- Fail-soft commit работает по минимальному dependency closure. Любой валидный partial/passthrough `vmf_out` означает exit code 0; exit code 1 означает, что корректный `vmf_out` передать не удалось.
- Корректный целый skin index вне текущей source table нормализуется в effective skin 0 и создаёт жёлтый warning. Недесятичный skin остаётся malformed request. Изменение original MDL/skin table инвалидирует все scale variants модели и заставляет повторно интерпретировать raw skin из исходного VMF.
- Normal cleanup управляет только новыми managed roots.
- Debug cleanup `-debug_cleanup 1` удаляет `models/psr_scaled`, `materials/models/psr_scaled` и cache текущей project identity. Режим `2` делает то же для текущего проекта, очищает cache/recovery/failed-run state всех известных project identities и общий PSR `work`, сохраняя logs; все известные project locks предварительно захватываются без ожидания.
- Legacy migration/cleanup является отдельной операцией.
- Покраска предпочитает Patch с fallback на полную VMT-копию.
- При выборе color-policy префикс `SDK_` является прозрачным alias базового shader name; это не разрешает неподдерживаемый базовый shader и не меняет shader name в generated/full-copy VMT.
- Compile-safe skin layout ограничен 31 уникальным material name и 1024 skin-family rows. Превышающая предел новая цветная вариация пропускается с warning, а entity использует исходный skin; отклонённый VMT не генерируется.
- Неиспользуемые colored mappings сохраняют стабильные индексы при обычных compile-run. Их удаление/сдвиг разрешены только явному project-wide cleanup с dry-run, предупреждением и перечнем карт/масштабов для обязательной перекомпиляции.
- В конце каждого compile-run печатается единый дедуплицированный отчёт из красных `ERROR`, жёлтых `WARNING` и нейтральных `INFO`. Рекомендации входят в соответствующие error/warning записи. Severity и exit code независимы; без поддержки цвета сохраняются текстовые метки.
- Project cache использует строго валидируемый versioned JSON manifest, изолированный нормализованной identity `GameInfo.txt`, и атомарный replace вместо pickle.
- Warm-cache reuse является проверкой текущего manifest и filesystem state, а не доверием к наличию имени: source/layout/параметры и хэши каждого material/model companion должны совпасть. Не прошедший проверку артефакт становится точечным generation miss; reused-файлы повторно проверяются перед commit.
- QC variants строятся из общего validated reference QC; generated filename использует compile-scale identity, `$scale` и LOD distances используют model-dependent geometry scale, collision сохраняется без числового переписывания, а top-level `$bbox`/`$cbox`/`$illumposition` удаляются для пересчёта StudioMDL. Viewport regression остаётся отдельным более широким уровнем проверки.
- Staging является operation-scoped и caller-owned: уникальный marker-protected root создаётся под явно заданным parent, source content повторно проверяется перед materialization, обычный cleanup не выходит за этот root. Неудачный run может явно сохранить staging для диагностики.
- Для SDK 2013 SP ожидаемый compiled model set — `.mdl`, `.vvd`, `.dx80.vtx`, `.dx90.vtx`, `.sw.vtx` и `.phy` при наличии collision. Exit code/строка `Completed` недостаточны: до commit обязательны проверка каждого файла, StudioMDL-представимого managed internal model name (не более 63 ASCII-байт в `name[64]`) и static-prop flag MDL.
- Сохранение `$collisionmodel`/`$collisionjoints` и замена pre-existing `$scale` подтверждены staged compile-validation на четырёх реальных cases. Explicit bounds компилировались, но дали неверный viewport culling и теперь удаляются без числового переписывания, чтобы их пересчитал StudioMDL. Top-level `$definebone` сохраняется для исходно static QC, но удаляется parser-aware при dynamic-to-static conversion; итоговый compiled MDL такой конверсии обязан иметь ровно одну render-кость под индексом 0.
- Первый 2.0 ограничен Source SDK 2013 SP.
- Поддерживаются только Windows 10/11 x64.
- Дистрибутив состоит из `props_scaling_recompiler.exe` и отдельного Crowbar в `third-party`; сторонние программы не встраиваются в основной exe, VPKEdit не поставляется.
- Project state хранится под `%LOCALAPPDATA%\PropsScalingRecompiler\projects\<project_id>`; одновременные run одного проекта запрещены project lock.
- Основные CLI-аргументы остаются `-game/-vmf_in/-vmf_out`; `-debug_cleanup 0/1/2` является отдельным явным destructive debug-переключателем, старые флаги принимаются только как deprecated с предупреждением.
- После каждого полезного изменения рабочего кода, поведения или сборочной конфигурации PSR необходимо выполнить проверенную release-сборку, сразу установить свежий `props_scaling_recompiler.exe` в `C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin\props_scaling_recompiler.exe` и подтвердить совпадение SHA-256 собранного и установленного файлов. Это постоянное разрешение пользователя относится только к данному EXE и предназначено для немедленного ручного тестирования; документационные изменения сами по себе пересборки не требуют.
- Предпочтительный бюджет основного exe — до 16 MiB, размер свыше 64 MiB блокирует release build.
- Архитектурная память хранится в этом документе, обязательные рабочие правила — в корневом `AGENTS.md`.
