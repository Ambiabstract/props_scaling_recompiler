# Production runtime validation на карте Antenna

Дата проверки: 2026-08-22.

## Цель и изоляция

Проверен полный production coordinator `discover -> plan -> generate -> validate -> commit` на копии `aa_models_color_tint_test_01a.vmf` с реальными Crowbar 0.68 и StudioMDL Source SDK 2013 Singleplayer. VMF, writable game root, `%LOCALAPPDATA%` и все generated assets находились во временном каталоге. SearchPaths исходного Antenna были раскрыты в конкретные read-only folder/VPK mounts в исходном порядке.

До и после запуска сравнивались SHA-256 исходного VMF и полные snapshots `models/psr_scaled` и `materials/models/psr_scaled` Antenna. Изменений внешнего проекта и SDK не обнаружено.

Проверенные входы:

- VMF SHA-256: `18aede35a65477a3cecd00b6e063de3e5807f5fb7388dd77c37f80958f57b69d`;
- Crowbar SHA-256: `4b5fc8f5092448c1f8fe12f6849bf8ee3996406f02109ec90ab800c6cf145b2a`;
- StudioMDL SHA-256: `e6c4ea7477b8ce31de878ff53ca640cb222c4978f3ba33c4715de3de1c7a6416`.

## Найденная и исправленная проблема

Первый настоящий cold-run обнаружил ограничение Crowbar 0.68: его файловые операции не поддерживают длинный physical staging path старше лимита Win32. Постоянная identity проекта осталась полным SHA-256 под `%LOCALAPPDATA%\PropsScalingRecompiler\projects\<project_id>`, а временный operation staging перенесён в короткий `%LOCALAPPDATA%\PropsScalingRecompiler\work\<project_prefix>`. Staging по-прежнему состоит из уникальных marker-protected roots; короткий prefix не используется как identity, cache или locking boundary.

StudioMDL теперь получает автоматически созданный staging `game/GameInfo.txt`: writable staging root идёт первым, затем в исходном порядке перечисляются конкретные source folders и VPK. Это исключает запись generated assets во внешний проект.

Crowbar 0.68 является обычным WinForms-приложением: его entry point после завершения command-line операции вызывает общий `Dispose`, поэтому инструмент сохраняет настройки в пользовательский AppData. В тестовом протоколе XML Crowbar сохранялся и гарантированно восстанавливался; его SHA-256 до и после равен `9a08266b7bc90898191fd21a5029813e92734d28e185aa45564ce7a8c80008a0`. См. [официальный entry point Crowbar](https://github.com/ZeqMacaw/Crowbar/blob/master/Crowbar/Core/-%20Application/Main.vb).

## Результат

Cold-run:

- 27 активных PSR-сущностей;
- 17 сгенерированных моделей;
- 8 сгенерированных цветных материалов;
- 108 опубликованных файлов;
- 7.522 секунды.

Tool-less warm-run на том же input:

- Crowbar и StudioMDL не передавались runtime;
- 17 моделей и 8 материалов переиспользованы после проверки cache/artifact identity;
- generated files не публиковались повторно;
- VMF и manifest побайтово совпали с результатом cold-run;
- 0.762 секунды, примерно в 9.9 раза быстрее cold-run.

Выходной VMF повторно структурно разобран. Для всех 27 затронутых entity подтверждены `prop_static`, managed model/skin mapping и удаление direct PSR-only keys. Повторный discovery не нашёл активных PSR requests.

## Автоматический запуск

Тест по умолчанию пропущен и требует установленного внешнего окружения:

```powershell
$env:PSR_RUN_EXTERNAL_RUNTIME = '1'
python -m pytest -q -s tests/test_external_runtime.py -m external_sdk
```

При нестандартных путях используются `PSR_ANTENNA_ROOT` и `PSR_SDK_ROOT`. Тест не является viewport/in-game визуальной проверкой оттенков; он доказывает production orchestration, реальную декомпиляцию/компиляцию, структурный VMF commit и warm-cache reuse.
