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
#   Основные константы
# ----------------------------------------
LOG_FILE = f"{os.path.splitext(os.path.basename(sys.argv[0]))[0]}_log.txt"

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
    # Ключ - имя уровня. Значение - множество страшных "ключей с 4 параметрами" из словаря scaled_props
    locations_and_props: Dict[str, Set[str]] = field(default_factory=dict)

# Класс глобального кэша
class GlobalCache:
    #Единый кэш: projects[project_name].assets[...] + метаданные
    def __init__(self, cache_file: Path):
        logger.info(f"GlobalCache __init__")
        self.path = cache_file
        self.projects: Dict[str, Project] = {}

    def load(self) -> None:
        logger.info(f"GlobalCache load")
        if self.path.exists():
            with self.path.open("rb") as f:
                self.projects = pickle.load(f)
        else:
            self.projects = {}

    def save(self) -> None:
        logger.info(f"GlobalCache save")
        with self.path.open("wb") as f:
            pickle.dump(self.projects, f)

    def get_project(self, gameinfo_path: str) -> Project: # *
        logger.info(f"GlobalCache get_project")
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
        self.args = args
        logger.debug(f"RecompilerApp self.args:\n{self.args}")
        # self.cache = GlobalCache(Path("props_scaling_recompiler_cache.pkl"))
        # self.cache.load()
    def run(self) -> int:
        logger.debug(f"RecompilerApp run")
        # Если дебаг параметр активирован, то включаем отображение сообщений дебаг уровня в консоли
        if self.args.debug == 1:
            ch = logging.StreamHandler()
            ch.setLevel(logging.DEBUG)
            logger.addHandler(ch)

        logger.debug(f"self.args.game: {self.args.game}")
        logger.debug(f"self.args.vmf_in: {self.args.vmf_in}")
        logger.debug(f"self.args.vmf_out: {self.args.vmf_out}")
        logger.debug(f"self.args.subfolders: {self.args.subfolders}")
        logger.debug(f"self.args.force_recompile: {self.args.force_recompile}")
        logger.debug(f"self.args.check_origs: {self.args.check_origs}")
        logger.debug(f"self.args.debug: {self.args.debug}")
        
        
        # 0. Быстрая проверка что в ВМФ вообще есть нужный класс из ФДГ.
        # 1. Валидация путей, кэша.
        # 2. Чтение VMF.
        # 3. Анализ что делать дальше (сверка с кэшем). Скорее всего покраска делается приоритетнее скейла, т.к. меняются исходные модели. Ещё возможно нужен флаг в класс оригов что был покрашен и нужно рекомпильнуть все дочерние поскейленные ассеты.
        # 4. Пайлайн:
        #   1) Найти реальные пути оригов под покраску, декомпильнуть, покрасить, скомпилировать.
        #   2) Найти реальные пути оригов под скейл, декомпильнуть, трансформ QC, скомпилировать.
        # 5. Обновить VMF.
        # 6. Обновить кэш.
        # Надо изучить какой способ чтения и записи VMF самые быстрые, потому что на это уходит больше всего времени почему-то.
        return 0
        
        создаём класс который управляет кэшем, загружаем кэш
        создаём класс который управляет чтением (и записью?) вмф
        считаем сколько в VMF поскейленных ассетов, если ноль - передаём исходный вмф на выход, если больше нуля то идём дальше

# ----------------------------------------
#   Функции
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

    logger = logging.getLogger(logger_name)
    # logger.setLevel(level)
    logger.setLevel(logging.DEBUG)   # принимаем всё, а фильтруют хэндлеры
    logger.handlers.clear()
    # logger.propagate = False возможно понадобится

    # Консольный хэндлер
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch.setFormatter(ColorFormatter("%(message)s"))
    logger.addHandler(ch)
    
    # Файловый хэндлер
    fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
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
        logger.critical(Fore.RED + f"ERROR! This .exe file should lie in the bin folder where the Source Engine tools such as hammer.exe, studiomdl.exe and so on lie.\nFor example: C:/Program Files (x86)/Steam/steamapps/common/Source SDK Base 2013 Singleplayer/bin")
        return False
    if not os.path.exists(os.path.join(script_path, "studiomdl.exe")):
        logger.critical(Fore.RED + f"ERROR! I can't find studiomdl.exe in this bin folder! This .exe should be put in the bin folder with tools, not with client.dll and server.dll.\nFor example: C:/Program Files (x86)/Steam/steamapps/common/Source SDK Base 2013 Singleplayer/bin")
        return False
    
    # Проверка на две сторонние тулзы
    if not any("CrowbarCommandLineDecomp.exe" in files for files in listdirs):
        logger.critical(Fore.RED + f"ERROR! This tool requires CrowbarCommandLineDecomp.exe lying in the same bin folder to work!")
        return False
    if not any("vpkeditcli.exe" in files for files in listdirs):
        logger.critical(Fore.RED + f"ERROR! This tool requires standalone vpkeditcli.exe lying in the same bin folder!")
        return False
    logger.debug(f"initial_check success")
    return True

# Функция которая собирает параметры запуска
def build_argparser() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"props_scaling_recompiler usage:")
    parser.add_argument("-game", type=str, required=True, help="Path to the game directory")
    parser.add_argument("-vmf_in", type=str, required=True, help="Path to the input .vmf file")
    parser.add_argument("-vmf_out", type=str, required=True, help="Path to the output .vmf file")
    parser.add_argument("-subfolders", type=int, required=False, default=1, help="Using subfolders (0 or 1)")
    parser.add_argument("-force_recompile", type=int, required=False, default=0, help="Recompile all props for this map (0 or 1)")
    parser.add_argument("-check_origs", type=int, required=False, default=0, help="Check hash-sum of original models (0 or 1)")
    parser.add_argument("-debug", type=int, required=False, default=0, help="debug mode")
    return parser.parse_args()

# ----------------------------------------
#   Мейн функция
# ----------------------------------------
def main() -> int:
    # logger = logging.getLogger("colored_logger") скорее всего не нужно
    logger.debug(f"main() started")
    if not initial_check(): return 1
    try:
        args = build_argparser()
    except:
        os.system('cls' if os.name == 'nt' else 'clear')
        logger.critical(f"ERROR! Input args not found!")
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
        if exit_code in (1, 130): input("\nPress Enter to exit...")
        logging.shutdown()
    sys.exit(exit_code)
