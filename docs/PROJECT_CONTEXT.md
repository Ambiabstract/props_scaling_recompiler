# Память проекта props_scaling_recompiler

Дата фиксации: 2026-08-20.

## Назначение

`props_scaling_recompiler` (PSR) — compile-time инструмент для Hammer++, который заменяет `prop_static_scalable` на обычный `prop_static` и автоматически создаёт необходимые варианты Source-моделей. Основные задачи 2.0:

- равномерное масштабирование модели вместе с геометрией, LOD и collision;
- преобразование подходящей dynamic/physics-модели в static-вариант;
- покраска static prop через дополнительные материалы и skin families;
- повторное использование готовых артефактов между картами;
- безопасная актуализация generated-контента.

Инструмент запускается отдельным этапом Hammer compile-run. Он получает `-game`, `-vmf_in` и `-vmf_out`, подготавливает ассеты и создаёт VMF для последующих стадий компиляции карты.

## Текущее состояние репозитория

- Основная ветка исследования: `psr_200_dev_002`.
- `props_scaling_recompiler_v1.1.2.py` — последняя релизная реализация и текущий поведенческий baseline.
- `props_scaling_recompiler.py` — незавершённый архитектурный прототип 2.0.
- Прототип 2.0 синтаксически корректен, но функционально не завершает pipeline: содержит интерактивные остановки, заглушки генерации QC/компиляции, не сохраняет новый кэш и не пишет `vmf_out`.
- Автоматических тестов и зафиксированных test fixtures пока нет.
- Исследовательские скрипты `is_staticprop.py`, `skins_from_mdl.py` и `mdl_skins_and_cdmaterials*.py` подтверждают возможность чтения static flag, material table, `$cdmaterials` и skin families непосредственно из MDL.
- Пользовательское незакоммиченное изменение в `props_scaling_recompiler.py`: версия `2.0.0 - dev 001` заменена на `2.0.0 - dev 002`.

## Целевая среда 2.0

Первый релиз PSR 2.0 поддерживает только:

- Windows;
- Source SDK 2013 Singleplayer;
- Hammer++;
- MDL и toolchain фактической SDK 2013 SP среды пользователя.

Garry's Mod, Portal 2 и другие ветки Source не входят в scope первого релиза 2.0.

Локальное референсное окружение пользователя:

- SDK bin: `C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin`;
- реальный мод: `C:\Program Files (x86)\Steam\steamapps\sourcemods\antenna_sdk2013`;
- production-комплект 1.1.2: `prod/props_scaling_recompiler_v1.1.2`.

Зафиксированные внешние инструменты:

| Инструмент | Версия | SHA-256 |
|---|---:|---|
| CrowbarCommandLineDecomp.exe | 0.68.0.0 | `4B5FC8F5092448C1F8FE12F6849BF8EE3996406F02109EC90AB800C6CF145B2A` |
| vpkeditcli.exe | 4.2.3 | `A28E5B596161995BEE529DF2FCB06F754482255B2306F697D4FEF4F6F79BEA2A` |
| studiomdl.exe из SDK 2013 SP | file version отсутствует | `E6C4EA7477B8CE31DE878FF53CA640CB222C4978F3BA33C4715DE3DE1C7A6416` |

Бинарники Crowbar и VPKEdit из production-комплекта побайтно совпадают с установленными в SDK `bin`.

## Утверждённые продуктовые правила

### Оригиналы

Оригинальные MDL, QC, VMT и companion-файлы никогда не изменяются. Все преобразования создают новые артефакты.

Оригинальная модель используется напрямую только при одновременном выполнении условий:

- она уже имеет static-prop flag;
- запрошенный scale равен 1.0;
- запрошенный цвет равен `255 255 255`.

Любая другая комбинация создаёт managed-модель PSR, включая dynamic при scale 1.0 и покрашенный static при scale 1.0.

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

Точное правило преобразования float scale в `XXX` ещё должно быть утверждено. Оно обязано быть валидируемым и не допускать тихих коллизий.

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

### GeneratedModel

- ссылка на SourceAsset;
- канонический scale;
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
- исходный запрос модели/scale/skin/color;
- GeneratedModel и итоговый skin index;
- версия последнего успешного анализа карты.

### ProjectCache / Manifest

- явная schema version;
- нормализованный project identity на основе GameInfo;
- SourceAsset, GeneratedModel, ColoredMaterial, SkinMapping и MapUsage как отдельные таблицы/коллекции;
- атомарная запись через временный файл и replace;
- возможность отклонить или мигрировать несовместимую схему;
- отсутствие зависимости от импортируемых Python dataclass через неустойчивый pickle-граф.

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

Нельзя записывать VMF, указывающий на неподтверждённый артефакт. Частично успешная генерация должна либо сохранить корректные ранее существовавшие артефакты, либо завершиться до commit-этапа с понятным отчётом.

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

## Cleanup и миграция

Нормальный cleanup 2.0 работает только внутри:

```text
models/psr_scaled/
materials/models/psr_scaled/
```

Эти каталоги считаются managed-пространством PSR. Агрессивная актуализация допустима только после построения полного плана, проверки cache/manifest и успешного разрешения источников.

Legacy-контент хранится в многочисленных `scaled` подпапках рядом с оригиналами. В Antenna обнаружено:

- 162 legacy-каталога с именем `scaled`/`psr_scaled` на момент инвентаризации, фактически новых `psr_scaled` ещё нет;
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
- end-to-end: no-op VMF, один static 1.0 white, dynamic 1.0, scaled static, colored static и сочетание нескольких карт.

Реальные VMF и тестовые ассеты пользователь добавит позднее. До этого интеграционные предположения должны быть помечены и покрываться synthetic fixtures.

## Открытые решения

- правило округления/пределов scale и формат канонического scale key;
- выбор `$color` или `$color2` по shader и поведение для неподдерживаемых shader'ов;
- точная семантика Patch `insert`/`replace` на SDK 2013 SP;
- способ fingerprint зависимостей для Patch из VPK;
- политика переноса legacy-generated модели, выбранной как новый original;
- формат cache/manifest: вероятный JSON или другая явная схема вместо pickle;
- обработка частичного успеха, когда одна из моделей карты не компилируется;
- допустимость параллельного запуска двух Hammer compile jobs для одного проекта;
- место и lifecycle staging/temp каталогов;
- точная политика cleanup для старых ревизий generated skin layout.

## Принятые решения

- Оригиналы неизменяемы.
- Generated-модели живут в `models/psr_scaled`.
- Generated-материалы живут в `materials/models/psr_scaled`.
- Используется только `_scaled_XXX`; `_static` не создаётся и не трактуется специально.
- Нейтральный static 1.0 использует оригинал; остальные случаи создают managed-модель.
- Normal cleanup управляет только новыми managed roots.
- Legacy migration/cleanup является отдельной операцией.
- Покраска предпочитает Patch с fallback на полную VMT-копию.
- Первый 2.0 ограничен Source SDK 2013 SP.
- Архитектурная память хранится в этом документе, обязательные рабочие правила — в корневом `AGENTS.md`.
