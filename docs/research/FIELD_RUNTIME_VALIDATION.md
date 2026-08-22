# Полевой runtime-прогон Antenna

Дата: 2026-08-22.

## Окружение

- проект: установленный `antenna_sdk2013`;
- toolchain: Crowbar 0.68, StudioMDL, VBSP, VVIS и VRAD из Source SDK Base 2013 Singleplayer;
- запускался настоящий one-file `props_scaling_recompiler.exe` из SDK `bin`;
- итоговая проверенная сборка: 11 433 202 байта (10,90 MiB), SHA-256 `66B85D71397C38B7E9C6524B1654AE05B592D4A22D45E2DBEFBF7E0AC8AA807D`.

До каждого изменения установленного EXE сохранялась отдельная копия в `dist/field-backup/2026-08-22`. Исходные VMF не перезаписывались; все диагностические входы и выходы создавались отдельно.

Первый автоматизированный запуск ошибочно передал `-w 640 -h 480 -windowed` без snapshot/restore и изменил Source registry пользователя. После диагностики профиль Antenna восстановлен и повторно проверен как `2560×1440`, fullscreen, `ScreenNoBorder=0`. Для всех будущих игровых тестов обязателен точный snapshot registry/CFG/video state и восстановление в `finally` после штатного выхода, timeout, принудительной остановки или crash; video launch arguments без необходимости запрещены.

## Успешный PSR/compile pipeline

### `aa_models_color_tint_test_01a.vmf`

- cold run: 27 сущностей, 17 generated models, 8 generated materials, 108 опубликованных файлов;
- итоговый VMF структурно валиден: 0 `prop_static_scalable`, 27 `prop_static`;
- VBSP завершился с exit code 0 без missing MDL/VMT;
- warm run: 17 reused models, 8 reused materials, 0 опубликованных файлов.

### `psr_test_01a.vmf`

- cold run: 49 сущностей, 22 generated models, 22 generated materials, 154 опубликованных файла;
- итоговый VMF структурно валиден: 0 `prop_static_scalable`, 46 `prop_static`, 3 `prop_dynamic` для `door02_double`;
- VBSP завершился с exit code 0 без missing MDL/VMT;
- warm run: 22 reused models, 22 reused materials, 0 опубликованных файлов.

Эти пункты подтверждают pipeline до fully compiled BSP, но не успешный игровой runtime: ниже зафиксирован отдельный crash generated `door02_double_scaled_*`.

### Игровая material-binding регрессия и исправление

Первый ручной запуск BSP выявил, что colored skin names были записаны в MDL полными logical paths `models/psr_scaled/...` при сохранённых исходных `$cdmaterials`. Движок складывал их и искал, например, `models/props_c17/models/psr_scaled/props_c17/...`, хотя физический VMT находился в `materials/models/psr_scaled/props_c17/...`. Три запуска завершились одинаковым `0xC0000005` в `shaderapidx9.dll` (offset `0xBA50D`, read address `0x1C`); без PDB это считается сильной корреляцией, а не доказанной единственной причиной crash.

QC transformer теперь добавляет `$cdmaterials "models/psr_scaled/"` и записывает managed skin names относительно него. Layout fingerprint получил отдельную material-binding version, поэтому ранее скомпилированные MDL не переиспользуются. Повторный cold run пересобрал затронутые модели; binary inspection подтвердил для canister root `models/psr_scaled/` плюс texture `props_c17/canister_propane01a_col_232_142_035`, а для fsmit — root плюс `apt/fshmit...`. VBSP завершился с exit code 0 без PSR missing-material diagnostics, а следующий warm run переиспользовал 22 модели и 22 материала без generation/publication. Первоначальный скрытый запуск прожил 25 секунд, но был слишком коротким и использовал VBSP-only/fullbright BSP; он не считается доказательством отсутствия crash.

Последующие ручные запуски продолжили падать. Все пять исходных и все диагностические dumps имеют один signature: `0xC0000005` в `shaderapidx9.dll+0xBA50D`, чтение `0x1C`, при этом `EAX=0`. Дизассемблирование показывает фактическую инструкцию `mov esi,dword ptr [eax+1Ch]` после virtual call, вернувшего null render object.

Parser-aware бинарное деление fully compiled `VBSP -> VVIS -> VRAD` карты дало следующую матрицу:

- исходная карта падает; с `r_drawstaticprops 0` стабильна 55 секунд;
- 24 colored props стабильны 42 секунды;
- 25 props со `skin 0` падают;
- 12 dynamic-to-static props падают;
- группа `monitor01 + door02_double` падает;
- три `monitor01` стабильны 42 секунды.

Следовательно, crash вызывает generated `door02_double_scaled_050/100/150`. Обратная декомпиляция `_scaled_100` показала bodygroup `handle02`, где option 0 — `_psr_empty_bodygroup_*.smd` с секцией `triangles` без единого треугольника; Crowbar также восстанавливает его как отдельный zero-vertex LOD model. StudioMDL принимает этот output, но runtime static-prop renderer получает null object и падает. Zero-triangle workaround отклонён как runtime-unsafe и удалён из production API.

Сравнение исходных MDL дало точный общий признак: у `door02_double` второй bodypart содержит три model options, а option 0 имеет пустое имя, `nummeshes=0`, `numvertices=0`; у безопасных `monitor01`, `doll01` и `cactus` все options непустые. Production inspector определяет dynamic MDL с пустым option многовариантного bodygroup непосредственно в `studiohdr`.

Промежуточный эксперимент компилировал такую дверь без `$staticprop` и доказал, что dynamic-вариант runtime-безопасен. Окончательный контракт версии 2.0 проще: PSR вообще не декомпилирует и не компилирует такой asset, не создаёт для него VMT/skin mappings и не учитывает старые cached scale variants. В output VMF исходные `model`, `modelscale`, `rendercolor` и `skin` сохраняются, classname становится `prop_dynamic`, а `convert_prop_to_static` удаляется.

Финальная полевая проверка VMF-only контракта на `psr_test_01a`:

- frozen run завершился успешно для 49 сущностей: 0 generated models, 19 reused обычных models, 0 generated materials, 22 reused materials, 0 published files;
- три двери entity 540/542/544 указывают на исходный `models/props_c17/door02_double.mdl`, имеют classname `prop_dynamic` и побайтно сохранённые `modelscale` `0.50`/`1.0`/`1.50`, `rendercolor` и `skin`;
- `convert_prop_to_static` у дверей отсутствует; output содержит 46 `prop_static` и три `prop_dynamic`;
- повторный warm-run дал идентичный VMF SHA-256 `9D25CBC1EC4255B20A1BACD86866B69DB5194D8C86161CA888CF70F431187178`;
- этот exact VMF полностью прошёл `VBSP -> VVIS -> VRAD` с exit code 0 без missing MDL/VMT. Новый игровой runtime не запускался: исходный dynamic MDL уже прошёл предыдущую длительную runtime-проверку, а текущая поправка не создаёт новый runtime asset.

Полевое подтверждение промежуточного compiled-dynamic эксперимента:

- настоящий Crowbar/StudioMDL успешно собрал все три door scale без static flag;
- итоговый VMF структурно содержит ровно 46 `prop_static` и три `prop_dynamic` (entity 540/542/544); тот эксперимент ещё удалял `modelscale`/`rendercolor`, что заменено окончательным passthrough-контрактом;
- door-only и полный BSP полностью прошли `VBSP -> VVIS -> VRAD`;
- door-only runtime достиг sign-on/render, оставался жив 75 секунд и не создал dump;
- полный runtime достиг sign-on/render и штатно завершился с code 0 примерно через минуту без dump; прежний `shaderapidx9.dll+0xBA50D` signature не воспроизведён;
- оба запуска выполнялись со snapshot/restore. После каждого повторно подтверждены `2560×1440`, fullscreen, `ScreenNoBorder=0`, исходный SHA-256 `config.cfg`; отсутствующие video-файлы остались отсутствующими. На полном запуске движок сам временно выполнил `2560×1440 -> 2048×1080`, что дополнительно подтвердило необходимость безусловного восстановления в `finally`.

На этой карте были обнаружены и устранены два реальных compatibility edge case:

1. `SDK_VertexLitGeneric` в Antenna является вариантом `VertexLitGeneric`, поддерживающим modulation. По принятому архитектурному правилу префикс `SDK_` теперь прозрачен для выбора color-policy: `SDK_VertexLitGeneric` и `SDK_UnlitGeneric` наследуют базовую `$color2` policy, а исходное shader name сохраняется.
2. StudioMDL падает при dynamic-to-static QC, если первым вариантом `$bodygroup` остаётся `blank`. Такие исходные MDL определяются до generation и остаются dynamic; zero-triangle placeholder не создаётся.

## `aa_models_static_convert_test_01a.vmf`

Оригинальная карта намеренно содержит compatibility/legacy cases и целиком не является happy-path fixture.

Полный исходный VMF корректно остановился до generation из-за 14 неразрешимых входов: 11 отсутствующих original models и 3 отсутствующих legacy `_scaled_...` ссылок вне managed root. Исходный VMF не изменился.

Для исследования была создана структурная копия без этих 14 сущностей. Она выявила ещё три границы:

1. Длинный `$modelname` физически создаётся StudioMDL под полным именем файла, но `studiohdr_t::name[64]` содержит только первые 63 ASCII-байта и NUL. Validator теперь строго сравнивает именно представимое 63-байтовое имя; неправильный префикс по-прежнему отклоняется. Полный длинный путь успешно прошёл последующий VBSP.
2. Для `furniturefridge001a` при утверждённом compile scale `0.01` StudioMDL создаёт MDL/VVD/VTX, но отбрасывает ставшую вырожденной collision geometry и не выпускает обязательный PHY. PSR корректно завершает операцию ошибкой `compiled_companion_missing` без частичной публикации.
3. Static-конверсия `renderng_regression_test_hunter.mdl` воспроизводимо завершает StudioMDL с `EXCEPTION_ACCESS_VIOLATION`. PSR возвращает `studiomdl_failed`, сохраняет staging для диагностики и ничего не публикует.

После структурного исключения двух StudioMDL edge cases поддерживаемая выборка прошла полностью:

- 102 сущности;
- cold run: 37 generated models, 22 reused models, 6 reused materials, 219 опубликованных файлов;
- итоговый VMF структурно валиден: 0 `prop_static_scalable`, 102 `prop_static`;
- VBSP exit code 0, missing MDL/VMT/model diagnostics отсутствуют;
- warm run: 59 reused models, 6 reused materials, 0 generated и 0 опубликованных файлов.

Первый вызов VBSP с длинным диагностическим basename не открыл VMF из-за собственного старого path buffer. Тот же VMF под коротким именем `psr_static_field.vmf` собрался успешно. Это ограничение следует учитывать в именах промежуточных файлов compile configuration.

## Состояние после прогона

Managed roots реального проекта содержат 433 model-файла размером 16 430 802 байта и 30 material-файлов размером 3 224 байта. Это подтверждённые результаты успешных транзакций; failed runs не изменяли roots, manifest или `vmf_out`.

Открытые решения:

- политика для collision, которую StudioMDL не может представить на экстремально малом масштабе: fail-closed остаётся текущим безопасным поведением;
- считать ли сложные dynamic ragdoll/flex модели вроде Hunter неподдерживаемыми для static conversion либо проектировать отдельную, доказанную simplification policy;
- полноценная будущая SMD-aware static-конвертация моделей с пустыми bodygroup options, включая корректные bodygroup/bone/collision transforms; это задача после 2.0, она не смешивается с текущим VMF-only dynamic fallback и не использует zero-triangle placeholders;
- отдельная ручная визуальная проверка для tint, skin indices, фактических размеров/collision и поведения door bodygroups; автоматический runtime-тест подтвердил отсутствие прежнего crash, но не заменяет визуальную оценку.
