# Source SDK 2013 SP: пределы материалов и skin families

Дата проверки: 2026-08-22.

## Результат

Для PSR 2.0 зафиксированы два независимых compile-safe предела:

- 31 уникальное material name во всей таблице skin families;
- 1024 строки `skinfamilies`.

Это не один и тот же лимит. Количество строк skin families может быть значительно больше числа уникальных материалов, если строки переиспользуют те же имена.

## Источник и проверка

Публичный Source SDK 2013 объявляет `MAXSTUDIOSKINS 32` с комментарием `total textures` в [`src/public/studio.h`](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/public/studio.h). Это размер/порог texture table, а не число family rows. На установленном пользователем `studiomdl.exe` с SHA-256 `e6c4ea7477b8ce31de878ff53ca640cb222c4978f3ba33c4715de3de1c7a6416` isolated compile с 31 уникальным material name успешен, а добавление 32-го завершается ненулевым exit code и сообщением `Too many materials used, max 32`. Поэтому допустимый входной максимум для PSR равен 31.

Отдельная матрица с повторяющимися material names показала:

- 32, 33, 256, 257 и 1024 rows компилируются;
- 1025 rows завершают StudioMDL с `EXCEPTION_ACCESS_VIOLATION`.

Все тесты выполнялись во временном `StagingWorkspace` с отдельным GameInfo overlay. Оригинальные Antenna и SDK assets не изменялись. Воспроизводимые opt-in cases находятся в `tests/test_external_sdk_generation.py` и запускаются только при `PSR_RUN_EXTERNAL_SDK=1`.

## Политика PSR

Planner проверяет будущие значения до добавления каждой colored variation. Вариант, превышающий 31 material name либо 1024 rows, не получает mapping и generated VMT. Он создаёт warning, а итоговый VMF assignment использует исходный skin. Уже переполненный cached/source layout считается ошибкой состояния и требует явного cleanup, а не попытки запуска StudioMDL.
