# Полевой runtime-прогон Antenna

Дата: 2026-08-22.

## Окружение

- проект: установленный `antenna_sdk2013`;
- toolchain: Crowbar 0.68, StudioMDL и VBSP из Source SDK Base 2013 Singleplayer;
- запускался настоящий one-file `props_scaling_recompiler.exe` из SDK `bin`;
- итоговая проверенная сборка: 11 437 528 байт (10,91 MiB), SHA-256 `A023D40A60A82762E1D7AE251C18A96F5473E31D0F6C3817EAE1853A6AD5939C`.

До каждого изменения установленного EXE сохранялась отдельная копия в `dist/field-backup/2026-08-22`. Исходные VMF не перезаписывались; все диагностические входы и выходы создавались отдельно.

## Успешные карты

### `aa_models_color_tint_test_01a.vmf`

- cold run: 27 сущностей, 17 generated models, 8 generated materials, 108 опубликованных файлов;
- итоговый VMF структурно валиден: 0 `prop_static_scalable`, 27 `prop_static`;
- VBSP завершился с exit code 0 без missing MDL/VMT;
- warm run: 17 reused models, 8 reused materials, 0 опубликованных файлов.

### `psr_test_01a.vmf`

- cold run: 49 сущностей, 22 generated models, 22 generated materials, 154 опубликованных файла;
- итоговый VMF структурно валиден: 0 `prop_static_scalable`, 49 `prop_static`;
- VBSP завершился с exit code 0 без missing MDL/VMT;
- warm run: 22 reused models, 22 reused materials, 0 опубликованных файлов.

На этой карте были обнаружены и устранены два реальных compatibility edge case:

1. `SDK_VertexLitGeneric` в Antenna является вариантом `VertexLitGeneric`, поддерживающим modulation. По принятому архитектурному правилу префикс `SDK_` теперь прозрачен для выбора color-policy: `SDK_VertexLitGeneric` и `SDK_UnlitGeneric` наследуют базовую `$color2` policy, а исходное shader name сохраняется.
2. StudioMDL падает при dynamic-to-static QC, если первым вариантом `$bodygroup` остаётся `blank`. PSR теперь только для такой конверсии создаёт staging-only zero-triangle SMD с тем же skeleton и подставляет его вместо `blank`, сохраняя порядок и индексы bodygroup options.

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
- отдельный будущий SMD-transform этап для исправления bone transforms и поворотов collision у dynamic-моделей; задача сознательно отложена и не смешивается с узким bodygroup workaround;
- отдельная визуальная проверка в Hammer/игре для tint, bodygroup placeholder, skin indices и фактических размеров/collision.
