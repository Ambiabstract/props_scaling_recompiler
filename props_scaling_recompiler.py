from __future__ import annotations
import os, re, io, sys
import logging
from logging.handlers import RotatingFileHandler
import argparse
import json, pickle, hashlib
import shutil
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from colorama import init as colorama_init
from colorama import Fore, Style

# ----------------------------------------
#   Константы
# ----------------------------------------
# Константы о программе
ABOUT_TOOL_VERSION      = "2.0.0 - dev 001"
ABOUT_TOOL_NAME         = Fore.CYAN + f"props_scaling_recompiler {ABOUT_TOOL_VERSION}" + Fore.RESET
ABOUT_TOOL_AUTHOR       = "Shitcoded by Ambiabstract (Sergey Shavin)."
ABOUT_TOOL_LINK         = "Github: https://github.com/Ambiabstract/props_scaling_recompiler"
ABOUT_TOOL_DISCORD      = "Discord: @Ambiabstract"

# Константы технические
TOOL_EXE_NAME = os.path.splitext(os.path.basename(sys.argv[0]))[0]
LOG_FILE = f"{TOOL_EXE_NAME}_log.txt"
LOG_MAXBYTES = 2_000_000
LOG_BACKUPCOUNT = 5
CACHE_FILE = f"{TOOL_EXE_NAME}_cache.pkl"
TEMP_FILES_FOLDER = f"{TOOL_EXE_NAME}_temp"

# Константы политические
SKIP_SEARCHPATHS_KEYS = {
        "platform",
        "game+mod+mod_write+default_write_path",
        "game_lv",
        "game+game_write",
        "gamebin",
    }
SKIP_IF_IN_NAME = {"_vo_", "_sound_", "_sounds_", "_lang_"}
SKIP_FOLDERS = {
        ".git", "bin", "cfg", "sound", "scripts", "modelsrc", "screenshots", "media",
        "materials", "mapsrc", "expressions", "maps", "particles", "scenes", "materialsrc",
        "resource", "sceneassets", "shadereditorui", "shaders",
        "vscript_io", "vscript", "vscripts"
    }

# ----------------------------------------
#   Классы
# ----------------------------------------

# Датакласс оригинального ассета
@dataclass
class OrigAsset:
    orig_hmr_rel_path: str          # хаммеровский путь оригинальной модели
    orig_real_path: str             # реальный путь к оригинальной модели (как в х++ включая пэкаджи всякие и кастом)
    orig_is_static: bool            # является ли оригинал изначально статик пропом
    orig_hash: str                  # хэш-сумма оригинальной модели для того чтобы отслеживать нужна ли рекомпиляция
    orig_cdmaterials: Set[str] = field(default_factory=set) # папки в которых хранятся VMT оригинала (отн. пути)
    orig_skinfamilies: List[List[str]] = field(default_factory=list) # список скинов с именами материалов
    orig_materials: Set[str] = field(default_factory=set) # множество относительных путей материалов
    orig_skin_map: Dict[Tuple[int, str], int] = field(default_factory=dict) # словарь-карта ремапа: ориг скин + цвет -> новый скин

# Датакласс псс и одновременно изменённого ассета
@dataclass
class PropStaticScalable:
    # Данные поскейленной модели, которые мы получаем на выходе
    scld_hmr_rel_path: str                  # хаммеровский путь поскейленной модели
    scld_skin: int                          # какой скин будет назначен поскейленной модели из-за покраски
    # Данные оригинальной модели и pss сущности
    orig_asset: OrigAsset                   # объект 
    pss_scale: float                        # скейл
    pss_skin: int                           # какой скин оригинальной модели используется для статик пропа
    pss_rendercolor: str = "255 255 255"    # цвет относительно оригинальной модели
    # scld_processed: bool = False    # скомпилено и лежит в проекте - я чёт сомневаюсь что мне этот флаг нужен

# Датакласс проекта
@dataclass
class Project:
    gameinfo_path: str              # абсолютный путь к гейминфо
    version: int = 1                # на случай будущих обновлений
    
    # Конструкция лютого словаря всех ассетов проекта, которые хотя бы раз были поскейлены/покрашены/статизированы.
    project_assets: Dict[
        str,                        # ключ - orig_hmr_rel_path
        Tuple[                      # связка оригинального ассета и словаря со всеми его измененными версиями
            OrigAsset,              # экземпляр класса OrigAsset со всей нужной инфой об ориг ассете
            Dict[                   # словарь всех изменённых ассетов scaled_props
                str,                # ключ - f"{orig_hmr_rel_path}_{scale_percent}_{pss_skin}_{pss_rendercolor}" *
                PropStaticScalable  # экземпляр класса PropStaticScalable со всей инфой об изменённом ассете
            ]
        ]
    ] = field(default_factory=dict)
    # * - тупое сложение через "_" четырёх самых важных параметров из entity vmf, а именно
    # хаммеровский путь оригинала, скейл в процентах как в имени нового мдл, скин pss и цвет pss, например:
    # "models/props/cs_militia/van.mdl_300_0_255_255_255"
    
    # Словарь использования изменённых ассетов на уровнях.
    locations_and_props: Dict[
        str,                # ключ словаря - имя уровня
        Tuple[              # значение - кортеж из двух множеств
            Set[str],       # множество страшных "ключей с 4 параметрами" из словаря scaled_props
            Set[            # множество кортежей "id энтити + страшный ключ"
                Tuple[
                    int,    # id энтити из хаммера
                    str     # страшный ключ
                ]
            ]
        ]
    ] = field(default_factory=dict)

# Класс глобального кэша
class GlobalCache:
    #Единый кэш: projects[project_name].assets[...] + метаданные
    def __init__(self, cache_file: Path):
        logger.debug(f"GlobalCache __init__")
        self.path = cache_file
        self.projects: Dict[str, Project] = {}

    def load(self) -> None:
        logger.debug(f"GlobalCache load")
        if self.path.exists():
            with self.path.open("rb") as f:
                self.projects = pickle.load(f)
        else:
            self.projects = {}

    def save(self) -> None:
        logger.debug(f"GlobalCache save")
        with self.path.open("wb") as f:
            pickle.dump(self.projects, f)

    def get_project(self, gameinfo_path: str) -> Project: # *
        logger.debug(f"GlobalCache get_project")
        p = self.projects.get(gameinfo_path) # есть ли у нас позиция в словаре с ключом gameinfo_path?
        if not p:
            p = Project(gameinfo_path=gameinfo_path) # если нету такого, то создаём новый экземпляр класса проект, назначаем ему путь гейминфо
            self.projects[gameinfo_path] = p # по ключу gameinfo_path добавляем новый проект
        return p
        # * - возможно стоит прям здесь входящий гейминфо путь обрабатывать так:
        # str(Path(gameinfo_path).as_posix()).lower()
        # но думаю что лучше там, где будет вызываться класс GlobalCache и этот метод get_project

# Тут будут другие классы

# Главный класс приложения
class RecompilerApp:
    def __init__(self, args: argparse.Namespace):
        logger.debug(f"RecompilerApp __init__")
        
        # Параметры запуска
        self.args = args
        logger.debug(f"RecompilerApp self.args:\n{self.args}")
        logger.debug(f"self.args.game: {self.args.game}")
        logger.debug(f"self.args.vmf_in: {self.args.vmf_in}")
        logger.debug(f"self.args.vmf_out: {self.args.vmf_out}")
        logger.debug(f"self.args.subfolders: {self.args.subfolders}")
        logger.debug(f"self.args.force_recompile: {self.args.force_recompile}")
        logger.debug(f"self.args.check_origs: {self.args.check_origs}")
        logger.debug(f"self.args.debug: {self.args.debug}")
        
        # Если дебаг параметр активирован, то включаем отображение сообщений дебаг уровня в консоли
        if self.args.debug == 1:
            for handler in logger.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.setLevel(logging.DEBUG)
        
        # Получаем имя VMF без расширения файла
        self.vmf_file_name, _ = os.path.splitext(os.path.basename(self.args.vmf_in.replace("\\", "/")))
        logger.debug(f"self.vmf_file_name: {self.vmf_file_name}")
        
        # Изначальное состояние, initial state
        self.cache = None
        self.project = None
    
    def run(self) -> int:
        logger.debug(f"RecompilerApp run")
        
        # Проверяем по быстрому сколько раз в файле встречается класс prop_static_scalable
        vmf_fast_check_count = vmf_fast_check(self.args.vmf_in)
        logger.info(f"{vmf_fast_check_count} prop_static_scalable entities found in {self.vmf_file_name}.vmf")
        if vmf_fast_check_count == 0: return 0 # если нету нужных энтитей - работа программы завершена
        
        # Получаем и проверяем гейминфо из game
        self.gameinfo_path = self.args.game + r"\gameinfo.txt"
        self.gameinfo_path = str(Path(self.gameinfo_path).as_posix()).lower()
        if not os.path.exists(self.gameinfo_path):
            logger.critical(f'ERROR! Gameinfo.txt not found! Please check "-game $gamedir" compile/run params in hammer run map options! \nSearch path: {self.args.game}')
            return 1
        logger.debug(f"self.gameinfo_path: {self.gameinfo_path}")
        
        # Получаем кэш и проект
        self.cache = GlobalCache(Path(CACHE_FILE))
        self.cache.load()
        logger.debug(f"self.cache:\n{self.cache}")
        logger.debug(f"self.cache.path:\n{self.cache.path}")
        logger.debug(f"self.cache.projects:\n{self.cache.projects}")
        
        # Получение проекта из кэша по гейминфо:
        self.project = self.cache.get_project(self.gameinfo_path)
        logger.debug(f"self.project:\n{self.project}")
        
        # Читаем пути из гейминфо
        # Возможно стоит хранить хэш-сумму для гейминфо и запускать этот парс только если он изменился?
        # В целом не сильно много времени занимает - 0.2 сек
        self.searchpaths = get_searchpaths(self.gameinfo_path)
        if not self.searchpaths:
            logger.critical(f'ERROR! Cant find correct SearchPaths in Gameinfo.txt!\nPlease check the file: {self.gameinfo_path}')
            return 1
        logger.info(f"Searchpaths successfully extracted")
        logger.debug(f"self.searchpaths:")
        for searchpath in self.searchpaths:
            logger.debug(f"{searchpath}")
        
        # Парсим энтити нужного класса из VMF
        raw_entities = parse_entities(self.args.vmf_in, classnames = {"prop_static_scalable"})
        if raw_entities:
            logger.info(f"Entities successfully extracted")
            logger.debug(f"raw_entities:")
            for raw_entity_id, raw_entity_keyvalues in raw_entities.items():
                logger.debug(f"{raw_entity_id}\t{raw_entity_keyvalues}")
                _, orig_hmr_rel_path, pss_scale_float, pss_rendercolor, pss_skin, _ = raw_entity_keyvalues
                logger.debug(f"\t{orig_hmr_rel_path}\t{pss_scale_float}\t{pss_rendercolor}\t{pss_skin}\n")
        else:
            logger.critical(f"ERROR! Can't read VMF to get entities!")
        
        # Попробуем генерировать страшные ключи
        # {orig_hmr_rel_path}_{scale_percent}_{pss_skin}_{pss_rendercolor}
        scary_keys = []
        for raw_entity_id, raw_entity_keyvalues in raw_entities.items():
            # Собираем страшный ключ
            _, orig_hmr_rel_path, pss_scale_float, pss_rendercolor, pss_skin, pss_origin = raw_entity_keyvalues
            scale_percent = int(float(pss_scale_float) * 100)
            scary_key = f"{orig_hmr_rel_path}_{scale_percent}_{pss_skin}_{pss_rendercolor.replace(' ', '_')}"
            
            # Тут надо сохранить старое содержание locations_and_props для последующего сравнения
            
            # Заполняем новый locations_and_props
            if self.vmf_file_name not in self.project.locations_and_props:
                self.project.locations_and_props[self.vmf_file_name] = (set(), set())
            self.project.locations_and_props[self.vmf_file_name][0].add(scary_key)
            self.project.locations_and_props[self.vmf_file_name][1].add((raw_entity_id, scary_key))
            
            # Тут сравнивамем старое и новое содержание locations_and_props для данной локации.
            # Разница - набор ассетов, наличие которых надо проверить в других локациях.
            # Если исчезнувшие ассеты не используются на других локациях - их надо удалить.
            
            # Заполняем project_assets
            if orig_hmr_rel_path not in self.project.project_assets:
                orig_asset = OrigAsset(orig_hmr_rel_path, "orig_real_path", "orig_is_static", "orig_hash")
                pss = PropStaticScalable("scld_hmr_rel_path", "scld_skin", orig_asset, pss_scale_float, pss_skin, pss_rendercolor)
                self.project.project_assets[orig_hmr_rel_path] = (orig_asset, {scary_key: pss})
        
        # Дебаг страшных ключей
        logger.debug(f"self.project.project_assets:")
        for key, tuple in self.project.project_assets.items():
            logger.debug(f"key: {key}")
            # logger.debug(f"tuple: {tuple}")
            orig_asset, dict = tuple
            logger.debug(f"orig_asset: {orig_asset}")
            for scary_key, psr_obj in dict.items():
                logger.debug(f"\tkey: {key}")
                logger.debug(f"\tpsr_obj: {psr_obj}\n")
        
        # Удаление лишнего если remove_unused = 1
        '''
        1. Если включён режим удаления неиспользованного, удаляем все поскейленные ассеты проекта, находящиеся вне папки models/scaled (избавляемся от легаси моделей)
        2. Если включён режим удаления неиспользованного, проходимся по locations_and_props и составляем список вариаций ассетов, которые ни разу не встретились. Проходимся по файлам проекта и удаляем их.
        '''
        
        # Актуализация оригинальных ассетов и первая пачка в очередь рекомпиляции если менялся ориг не нашей программой, чем-то извне
        '''
        1. Если включён режим проверки хэшей оригиналов (check_origs = 1), то по orig_hmr_rel_path ищем реальный путь каждого ориг ассета и сверяем хэш. Если хэш отличается - сразу актуализируем данные ориг ассета и добавляем все его вариации в очередь на перекомпиляцию.
        2. Смотрим по orig_hmr_rel_path (именно так!) каких оригинальных моделек ещё нету в кэше в project_assets.
            Для каждого orig_hmr_rel_path, которого нет в project_assets, запускаем метод/функцию, которая заполняет информацию об этих ориг ассетах в project_assets (объекты OrigAsset, но словарь изменённых ассетов естественно пустой, раз ни разу не встречался оригинал).
        '''
        
        # (До)заполняем очередь на (ре)компиляцию.
        '''
        Проходимся по оригинальным ассетам проекта и сверяем по scary_keys каких вариаций ещё нет в кэше, добавляем такие вариации в очередь на компиляцию.
        '''
        
        # Защита от удалённых не нашей программой поскейленных ассетов.
        '''
        Если включён режим доп проверки, то для всех ассетов проекта проходимся по версиям ассетов и проверяем что они действительно есть в проекте.
        Если какой-то вариации нету - добавляем в очередь на компиляцию.
        '''
        
        # Проходимся по очереди на компиляцию.
        '''
        Во главе угла - ориг ассет.
        Для каждого ориг ассета:
            - находим ориг модель, вытаскиваем и декомпилируем
            - [покраска] вычисляем всякую хуйню по сочетаниям скинов и материалов, редактируем QC оригинала
            - [покраска] находим ориг материалы, создаём копии VMT в соответствии с вычислениями, кладём в нужное место
            - [скейлинг] копируем QC для каждой вариации скейла и меняем параметры (скейл для разных типов, статик проп если был динамик и тд)
            - компилируем оригинал и все вариации
            - проверяем что всё доехало куда надо, заполняем данные о получившихся приколах в project_assets
        '''
        # Сохраняем кэш.
        '''
        '''
        
        # Заполняем VMF.
        '''
        Одним проходом читаем старый вмф и при этом сразу пишем тмп нового вмф, меняя строки по правилам (это будет нмного быстрее чем было ранее).
        По реальным путям из кэша наличие вариации ассета не проверяем, это лишнее время. Все проверки до этого должны были закрыть эти дыры, задача этого этапа - только запись нового ВМФ.
        Передаём получившийся ВМФ на выход.
        '''
        
        # Единый список проблем.
        '''
        В конце даём сводку ВСЕХ ошибок и недочётов которые были найдены на протяжении всего процесса работы программы. Один список в конце - намного удобнее, чем читать весь кэш.
        '''
        
        
        # старая заметка
        '''
        Разделять на покраску и скейл не будем, всё равно и то и другое надо через трансформ QC делать, а в обоих случаях у нас будет доступ к QC оригинала.
        Передавать в обработчик будем список в формате ?????
        '''

        
        return 0
        

        # 2. Чтение VMF.
        # 3. Анализ что делать дальше (сверка с кэшем). Скорее всего покраска делается приоритетнее скейла, т.к. меняются исходные модели. Ещё возможно нужен флаг в класс оригов что был покрашен и нужно рекомпильнуть все дочерние поскейленные ассеты.
        # 4. Пайлайн:
        #   1) Найти реальные пути оригов под покраску, декомпильнуть, покрасить, скомпилировать.
        #   2) Найти реальные пути оригов под скейл, декомпильнуть, трансформ QC, скомпилировать.
        # 5. Обновить VMF.
        # 6. Обновить кэш.
        # Надо изучить какой способ чтения и записи VMF самые быстрые, потому что на это уходит больше всего времени почему-то.
        
        # создаём класс который управляет кэшем, загружаем кэш
        # создаём класс который управляет чтением (и записью?) вмф
        # считаем сколько в VMF поскейленных ассетов, если ноль - передаём исходный вмф на выход, если больше нуля то идём дальше

# ----------------------------------------
#   Внешние функции
# ----------------------------------------

# Сетап логгера
def setup_logging(
    logger_name: str = "colored_logger",
    level: int = logging.DEBUG,
    good_level_value: int = 25,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> logging.Logger:
    colorama_init(autoreset=True)

    logging.addLevelName(good_level_value, "GOOD")

    def good(self, message, *args, **kwargs):
        if self.isEnabledFor(good_level_value):
            self._log(good_level_value, message, args, **kwargs)

    logging.Logger.good = good  # type: ignore[attr-defined]
    
    class ColorFormatter(logging.Formatter):
        COLORS = {
            logging.DEBUG: Fore.MAGENTA,
            # logger.info: Fore.GREEN,
            good_level_value: Fore.GREEN,
            logging.WARNING: Fore.YELLOW,
            logging.ERROR: Fore.RED,
            logging.CRITICAL: Fore.RED,
        }

        def format(self, record):
            log_color = self.COLORS.get(record.levelno, "")
            message = super().format(record)
            return f"{log_color}{message}{Style.RESET_ALL}"

    # Создаём и настраиваем логгер
    logger = logging.getLogger(logger_name)
    # logger.setLevel(level)
    logger.setLevel(logging.DEBUG)   # принимаем всё, а фильтруют хэндлеры
    logger.handlers.clear()
    # logger.propagate = False # возможно понадобится

    # Консольный хэндлер
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch.setFormatter(ColorFormatter("%(message)s"))
    logger.addHandler(ch)
    
    # Файловый хэндлер
    fh = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAXBYTES, backupCount=LOG_BACKUPCOUNT, encoding="utf-8")
    fh.setLevel(file_level)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)
    
    # Старый логгер
    '''
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    root = logging.getLogger()
    root.setLevel(logger.info)

    # Хэндлер консоли
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(fmt))
    root.addHandler(ch)

    # Хэндлер в файл (и ротация, чтобы не разрастался)
    fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt))
    root.addHandler(fh)
    '''
    return logger

# Функция чтобы узнавать путь к тулзе (py/exe)
def get_script_path():
    return os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))

# Функция для стартовой проверки
def initial_check():
    script_path = get_script_path()
    script_path_tp = script_path + r"\third-party"
    folder_name = os.path.basename(script_path)
    listdirs = [os.listdir(script_path)] + ([os.listdir(script_path_tp)] if os.path.exists(script_path_tp) else [])
    logger.debug(f"initial_check start")
    logger.debug(f"script_path: {script_path}")
    logger.debug(f"script_path_tp: {script_path_tp}")
    
    # Проверка что лежим в нужной папке bin
    if folder_name != "bin":
        logger.critical(f"ERROR! This .exe file must be located in the bin folder where the Source Engine tools such as hammer.exe, studiomdl.exe, and others are located.\nFor example: C:/Program Files (x86)/Steam/steamapps/common/Source SDK Base 2013 Singleplayer/bin")
        return False
    if not os.path.exists(os.path.join(script_path, "studiomdl.exe")):
        logger.critical(f"ERROR! Сan't find studiomdl.exe in this bin folder! This tool file must be placed in the bin folder with other tools, not with client.dll and server.dll.\nFor example: C:/Program Files (x86)/Steam/steamapps/common/Source SDK Base 2013 Singleplayer/bin")
        return False
    
    # Проверка на две сторонние тулзы
    if not any("CrowbarCommandLineDecomp.exe" in files for files in listdirs):
        logger.critical(f"ERROR! This tool requires CrowbarCommandLineDecomp.exe to be present in the same bin folder in order to work!")
        return False
    if not any("vpkeditcli.exe" in files for files in listdirs):
        logger.critical(f"ERROR! This tool requires the standalone vpkeditcli.exe to be present in the same bin folder!")
        return False
    logger.debug(f"initial_check success")
    return True

# Функция которая создаёт парсер параметров запуска
def build_argparser():
    parser = argparse.ArgumentParser(description=f"props_scaling_recompiler usage:")
    parser.add_argument("-game", type=str, required=True, help="Path to the game directory ($gamedir).")
    parser.add_argument("-vmf_in", type=str, required=True, help="Path to the input .vmf file ($path\$file.vmf)")
    parser.add_argument("-vmf_out", type=str, required=True, help="Path to the output .vmf file ($path\psr_temp\$file.vmf)")
    parser.add_argument("-subfolders", type=int, required=False, default=1, help="LEGACY - DO NOT USE!")
    parser.add_argument("-check_origs", type=int, required=False, default=1, help="(Optional) Check hash-sum of original models. If its not same as before - all scaled versions will be recompiled, because original asset has changed since last time we checked. 1 - turn on, 0 - turn off. Default - 1.")
    parser.add_argument("-remove_unused", type=int, required=False, default=0, help="(Optional) Checks cached maps and deletes unused scaled assets. Also deletes legacy-located scaled props (not from models/scaled folder). 1 - turn on, 0 - turn off. Default - 0.")
    parser.add_argument("-force_recompile", type=int, required=False, default=0, help="(Optional) Recompile all props for this map. 1 - turn on, 0 - turn off. Default - 0.")
    parser.add_argument("-debug", type=int, required=False, default=0, help="(Optional) Debug mode. Shows all debug messages in console. 1 - turn on, 0 - turn off. Default - 0.")
    return parser

# Функция для быстрого подсчёта количества prop_static_scalable энтитей в ВМФ
def vmf_fast_check(vmf_in):
    pattern = re.compile(r'"classname"\s+"prop_static_scalable"')
    with open(vmf_in, "r", encoding="cp1252") as f:
        text = f.read()
    return len(pattern.findall(text))

def get_searchpaths(gameinfo_path: str):
    logger.info(f"Getting paths from Gameinfo.txt")
    logger.debug(f"get_searchpaths gameinfo_path: {gameinfo_path}")
    
    # |all_source_engine_paths|
    all_source_engine_paths = os.path.abspath(os.path.join(get_script_path(), "../../half-life 2"))
    all_source_engine_paths = str(Path(all_source_engine_paths).as_posix()).lower()+"/"
    logger.debug(f"all_source_engine_paths: {all_source_engine_paths}")
    
    # |gameinfo_path|
    gameinfo_folder_path = gameinfo_path.replace(os.path.basename(gameinfo_path), "")
    logger.debug(f"gameinfo_folder_path: {gameinfo_folder_path}")
    
    # Список кортежей
    searchpaths = [] # (path_type, path) ("folder_materials" / "folder_models" / "vpk")
    
    in_searchpaths_block = False
    with open(gameinfo_path, "r", encoding="utf-8") as f:
        logger.debug(f"get_searchpaths gameinfo opened")
        for line in f:
            s = line.strip()
            if not s or s.startswith("//"): continue
            s = s.lower()
            if s.startswith("searchpaths"):
                in_searchpaths_block = True
                continue
            if in_searchpaths_block and s.startswith("}"): break
            if not in_searchpaths_block: continue
            s = s.split("//", 1)[0].strip()
            if not s: continue
            # s = s.replace("\\", "/") # на всякий случай
            parts = [part.strip('"') for part in s.split(maxsplit=1)]
            # logger.debug(f"s: {s}")
            # logger.debug(f"parts: {parts}")
            # input("Солёный чай")
            if len(parts) != 2:
                continue
            key, path = parts
            if key in SKIP_SEARCHPATHS_KEYS: continue
            
            if "|all_source_engine_paths|" in path: path = path.replace("|all_source_engine_paths|", all_source_engine_paths)
            if "|gameinfo_path|" in path: path = path.replace("|gameinfo_path|", gameinfo_folder_path)
            
            # Дефолтный тип
            path_type = "folder" # ("folder_materials" / "folder_models" / "vpk")
            
            # Валидация VPK
            if path.endswith(".vpk"):
                path_type = "vpk"
                if any(name_part in path for name_part in SKIP_IF_IN_NAME): continue
                if os.path.exists(path):
                    logger.debug(f"VPK found: {path}")
                    searchpaths.append((path_type, path))
                elif os.path.exists(path.replace(".vpk", "_dir.vpk")):
                    logger.debug(f'VPK found: {path.replace(".vpk", "_dir.vpk")}')
                    searchpaths.append((path_type, path.replace(".vpk", "_dir.vpk")))
                else:
                    logger.warning(f"VPK not found: {path}")
            
            # Валидация всего что под звёздочкой
            if path.endswith("*"):
                root_folder = path.replace("/*", "")
                for dirpath, dirnames, filenames in os.walk(root_folder):
                    # norm_path = os.path.normpath(dirpath) я думаю это нахуй не нужно, но на всякий случай пусть пока будет тут
                    norm_path = dirpath
                    # Проверяем "/models/"
                    if norm_path.endswith(os.path.join("models")):
                        # исключаем если внутри "/materials/models/"
                        if "materials" + os.sep + "models" not in norm_path.replace("/", os.sep):
                            path_type = "folder_models"
                            star_path = norm_path.lower().replace("\\", "/")
                            logger.debug(f"[*] Models folder found: {star_path}")
                            searchpaths.append((path_type, star_path))
                    # Проверяем "/materials/"
                    if norm_path.endswith(os.path.join("materials")):
                        path_type = "folder_materials"
                        star_path = norm_path.lower().replace("\\", "/")
                        logger.debug(f"[*] Materials folder found: {star_path}")
                        searchpaths.append((path_type, star_path))
                    # Проверяем ".vpk" файлы
                    for f in filenames:
                        if f.lower().endswith(".vpk"):
                            path_type = "vpk"
                            vpk_path = os.path.join(norm_path, f).lower().replace("\\", "/")
                            if any(name_part in vpk_path for name_part in SKIP_IF_IN_NAME): continue
                            logger.debug(f"[*] VPK found: {vpk_path}")
                            searchpaths.append((path_type, vpk_path))
            
            # Валидация папок без подпапок
            if path.endswith("."):
                path_models = path.replace(".", "") + "models"
                path_materials = path.replace(".", "") + "materials"
                if os.path.exists(path_models):
                    path_type = "folder_models"
                    logger.debug(f"[.] Models folder found: {path_models}")
                    searchpaths.append((path_type, path_models))
                if os.path.exists(path_materials):
                    path_type = "folder_materials"
                    logger.debug(f"[.] Materials folder found: {path_materials}")
                    searchpaths.append((path_type, path_materials))
                    
            # Всё остальное
            path_models = path + "models"
            path_materials = path + "materials"
            if os.path.exists(path_models):
                path_type = "folder_models"
                logger.debug(f"[f] Models folder found: {path_models}")
                searchpaths.append((path_type, path_models))
            if os.path.exists(path_materials):
                path_type = "folder_materials"
                logger.debug(f"[f] Materials folder found: {path_materials}")
                searchpaths.append((path_type, path_materials))
            
    '''
    logger.debug(f"\nsearchpaths:")
    for searchpath in searchpaths:
        logger.debug(f"{searchpath}")
    '''
    return searchpaths

# Парсер VMF
def parse_entities(vmf_path, classnames = {"prop_static_scalable"}):
    logger.info(f"Reading VMF...")
    results = {}
    with open(vmf_path, 'r', encoding="cp1252") as f:
        lines = iter(f)
        for line in lines:
            line = line.strip()
            if line == "entity":
                block = {}
                depth = 0
                for line in lines:
                    line = line.strip()
                    if line == "{":
                        depth += 1
                        continue
                    if line == "}":
                        depth -= 1
                        if depth == 0:
                            break
                        continue
                    if depth > 0 and line.startswith('"'):
                        try:
                            key, value = line.split('" "')
                            key = key.strip('"')
                            value = value.strip('"')
                            block[key] = value
                        except ValueError:
                            continue
                if block.get("classname") in classnames:
                    entity_id = block.get("id")
                    if entity_id:
                        model = block.get("model")
                        modelscale = block.get("modelscale")
                        origin = block.get("origin")
                        
                        # Сначала проверка правильности скейла
                        try:
                            if modelscale is not None:
                                float(modelscale)
                        except (ValueError, TypeError):
                            logger.warning(f'Warning! Model scale of "{os.path.basename(model)}" is wrong! Current value: "{modelscale}". Entity ID: {entity_id}. Entity origin: "{origin}". Compiling with scale 1.')
                            modelscale = "1"
                        if float(modelscale) < 0.01:
                            logger.error(f'ERROR! Model "{os.path.basename(model)}" has wrong scale: "{modelscale}". Should be more than 0.01. Entity ID: "{entity_id}". Entity origin: "{origin}". Skipping!')
                            modelscale = "1"
                        
                        # Теперь обработка поскейленных моделей которые юзаются как оригиналы
                        if model.count("_scaled_") >= 2:
                            logger.error(f"ERROR! Multiple times scaled model unsed as non-scaled. Name: {os.path.basename(model)}. ID: {entity_id}. Origin: {origin}. Skipping!")
                            continue
                        if "_scaled_" in model:
                            logger.warning(f"Warning! Scaled model unsed as non-scaled. Name: {os.path.basename(model)}. ID: {entity_id}. Origin: {origin}.")
                            logger.debug(f"Calculating new scale...")
                            parts = model.split("_scaled_")
                            scale_from_name = float(parts[1].replace(".mdl", "")) / 100
                            logger.debug(f"scale_from_name: {scale_from_name}")
                            modelscale = round(scale_from_name * float(modelscale), 2)
                            logger.debug(f"new modelscale: {modelscale}")
                            base_name = parts[0]
                            logger.debug(f"base_name: {base_name}")
                            logger.debug(f"old model hammer path: {model}")
                            # model = base_name + "_scaled_" + str(int(modelscale * 100)) + ".mdl" # это потом, щас нам нужен хаммеровский путь оригинального непоскейленного ассета
                            model = base_name + ".mdl"
                            logger.debug(f"new model hammer path: {model}")
                        
                        results[entity_id] = (
                            block.get("classname"),
                            model,
                            modelscale,
                            block.get("rendercolor", "255 255 255"),
                            block.get("skin", "0"),
                            block.get("origin"),
                        )
    return results

# ----------------------------------------
#   Мейн функция
# ----------------------------------------
def main() -> int:
    # logger = logging.getLogger("colored_logger") скорее всего не нужно
    logger.debug(f"main() started")
    logger.info(ABOUT_TOOL_NAME)
    logger.info(ABOUT_TOOL_AUTHOR)
    logger.info(ABOUT_TOOL_LINK)
    logger.info(ABOUT_TOOL_DISCORD+"\n")
    if not initial_check(): return 1
    parser = build_argparser()
    try:
        args = parser.parse_args()
    except:
        os.system('cls' if os.name == 'nt' else 'clear')
        logger.critical(f"ERROR! The input arguments are invalid!")
        parser.print_help()
        return 1
    return RecompilerApp(args).run()
    return 0

# ----------------------------------------
#   Концовочка с подхватами
# ----------------------------------------    
if __name__ == "__main__":
    colorama_init()
    setup_logging()
    logger = logging.getLogger("colored_logger")
    exit_code = 0
    logger.debug(f'\n\n========================================================================================\n===================================== NEW COMPILE ======================================\n=========================== Start date: {time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(time.time()))} ============================\n========================================================================================')
    try:
        start_time = time.time()
        exit_code = int(main())
    except KeyboardInterrupt:
        logger.info(f"Interrupted by user (Ctrl+C)")
        exit_code = 130
    except SystemExit as e:
        logger.exception("SystemExit")
        logger.exception(e)
        exit_code = 1
    except Exception:
        logger.exception("Unhandled exception")
        exit_code = 1
    finally:
        elapsed_time = time.time() - start_time
        hours, remainder = divmod(elapsed_time, 3600)
        minutes, seconds = divmod(remainder, 60)
        logger.good(f"\nprops_scaling_recompiler has finished its work!")
        logger.info(f"Time spent: {int(hours)} hours, {int(minutes)} minutes, {seconds:.2f} seconds\n")
        if exit_code in (1, 130): input("\nPress Enter to exit...")
        logging.shutdown()
    sys.exit(exit_code)
