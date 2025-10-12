from __future__ import annotations
import os, re, io, sys
import logging
from logging.handlers import RotatingFileHandler
import argparse
import json, pickle, hashlib
import shutil
import subprocess
import time
import struct, hashlib
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

# Константы для чтения флага статик пропа из мдл
MDL_IDST_LE = 0x54534449  # mdl little-endian
MDL_STATIC_PROP_FLAG = 1 << 4
MDL_FLAGS_OFFSET = 0x98  # смещение поля studiohdr_t::flags для мдл
MDL_IDST = b'IDST'

# Оффсеты studiohdr_t (Source MDL v44/46/48) для чтения инфы о материалах из мдл
MDL_OFF_TEXTURE_COUNT   = 0xCC  # int numtextures
MDL_OFF_TEXTURE_INDEX   = 0xD0  # int textureindex (file offset from start of studiohdr)
MDL_OFF_TXDIR_COUNT     = 0xD4  # int numcdtextures
MDL_OFF_TXDIR_INDEX     = 0xD8  # int cdtextureindex (file offset to array of int offsets-to-strings)
MDL_OFF_SKINREF_COUNT   = 0xDC  # int numskinref
MDL_OFF_SKINFAM_COUNT   = 0xE0  # int numskinfamilies
MDL_OFF_SKIN_INDEX      = 0xE4  # int skinindex (file offset to USHORT table)
MDL_STUDIOHDR_SIZE_MIN  = 0x1A0 # safe lower bound for header
MDL_MSTEXTURE_SIZE      = 64    # sizeof(mstudiotexture_t) in file
MDL_OFF_MSTEX_NAMEOFF   = 0x00  # int name_offset (relative to start of this struct)

# Константы политические
SKIP_SEARCHPATHS_KEYS = {
        "platform",
        "game+mod+mod_write+default_write_path",
        "game_lv",
        "game+game_write",
        "gamebin",
    }
SKIP_IF_IN_NAME = {"_vo_", "_sound_", "_sounds_", "_lang_"}
SKIP_FOLDERS = {".git", "bin", "cfg", "sound", "scripts", "modelsrc", "screenshots", "media", "mapsrc", "expressions", "maps", "particles", "scenes", "materialsrc", "resource", "sceneassets", "shadereditorui", "shaders", "vscript_io", "vscript", "vscripts"}

# ----------------------------------------
#   Классы
# ----------------------------------------

# Датакласс оригинального ассета
@dataclass
class OrigAsset:
    orig_hmr_rel_path: str          # хаммеровский путь оригинальной модели
    orig_full_path: str             # реальный путь к оригинальной модели (как в х++ включая пэкаджи всякие и кастом)
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
        logger.debug(f"self.args.check_origs: {self.args.check_origs}")
        logger.debug(f"self.args.remove_unused: {self.args.remove_unused}")
        logger.debug(f"self.args.force_recompile: {self.args.force_recompile}")
        logger.debug(f"self.args.debug: {self.args.debug}")
        
        # Если дебаг параметр активирован, то включаем отображение сообщений дебаг уровня в консоли
        if self.args.debug == 1:
            for handler in logger.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.setLevel(logging.DEBUG)
        
        # Получаем имя VMF без расширения файла
        self.vmf_file_name, _ = os.path.splitext(os.path.basename(self.args.vmf_in.replace("\\", "/")))
        logger.debug(f"self.vmf_file_name: {self.vmf_file_name}")
        
        # Назначаем пути вспомогательных программ
        self.vpkeditcli_path = (
            os.path.join(get_script_path(), "third-party", "vpkeditcli.exe")
            if os.path.exists(os.path.join(get_script_path(), "third-party", "vpkeditcli.exe"))
            else os.path.join(get_script_path(), "vpkeditcli.exe")
        )
        if not os.path.exists(self.vpkeditcli_path):
            logger.critical(f'Something wrong with vpkeditcli.exe path! Please report about this bug!')
        self.ccld_path = (
            os.path.join(get_script_path(), "third-party", "CrowbarCommandLineDecomp.exe")
            if os.path.exists(os.path.join(get_script_path(), "third-party", "CrowbarCommandLineDecomp.exe"))
            else os.path.join(get_script_path(), "CrowbarCommandLineDecomp.exe")
        )
        if not os.path.exists(self.ccld_path):
            logger.critical(f'Something wrong with CrowbarCommandLineDecomp.exe path! Please report about this bug!')
        
        # Изначальное состояние, initial state
        self.cache = None
        self.project = None
        self.project_files_index = {}
    
    def run(self) -> int:
        logger.debug(f"RecompilerApp run")
        
        # Проверяем по быстрому сколько раз в файле встречается класс prop_static_scalable
        vmf_fast_check_count = vmf_fast_check(self.args.vmf_in)
        logger.info(f"{vmf_fast_check_count} prop_static_scalable entities found in {self.vmf_file_name}.vmf")
        if vmf_fast_check_count == 0: return 0 # если нету нужных энтитей - работа программы завершена успешно
        
        # Получаем и проверяем гейминфо из game
        self.gameinfo_path = self.args.game + r"\gameinfo.txt"
        self.gameinfo_path = str(Path(self.gameinfo_path).as_posix()).lower()
        if not os.path.exists(self.gameinfo_path):
            logger.critical(f'ERROR! Gameinfo.txt not found! Please check "-game $gamedir" compile/run params in hammer run map options! \nSearch path: {self.args.game}')
            return 1
        logger.debug(f"self.gameinfo_path: {self.gameinfo_path}")
        
        # Сразу вычисляем путь к проекту где лежит гейминфо
        self.gameinfo_folder_path = self.gameinfo_path.replace(os.path.basename(self.gameinfo_path), "")
        logger.debug(f"self.gameinfo_folder_path: {self.gameinfo_folder_path}")
        
        # Получаем кэш
        self.cache = GlobalCache(Path(CACHE_FILE))
        self.cache.load()
        '''
        # logger.debug(f"self.cache:\n{self.cache}")
        # logger.debug(f"self.cache.path:\n{self.cache.path}")
        # logger.debug(f"self.cache.projects:\n{self.cache.projects}")
        '''
        
        # Получение проекта из кэша по гейминфо:
        self.project = self.cache.get_project(self.gameinfo_path)
        '''
        # logger.debug(f"self.project:\n{self.project}")
        '''
        
        # Читаем пути из гейминфо
        self.searchpaths = get_searchpaths(self.gameinfo_path)
        if not self.searchpaths:
            logger.critical(f'ERROR! Cant find correct SearchPaths in Gameinfo.txt!\nPlease check the file: {self.gameinfo_path}')
            return 1
        logger.info(f"Searchpaths successfully extracted")
        '''
        # logger.debug(f"self.searchpaths:")
        # for searchpath in self.searchpaths:
            # logger.debug(f"{searchpath}")
        '''
        
        # Сразу читаем содержимое всех доступных ВПК, чтобы не делать это для каждой модели
        self.build_vpk_trees()
        
        # Парсим энтити нужного класса из VMF
        d_eid_pss_data = parse_entities(self.args.vmf_in, classnames = {"prop_static_scalable"})
        if d_eid_pss_data:
            logger.info(f"Entities successfully extracted")
            '''
            # logger.debug(f"d_eid_pss_data:")
            # for eid_pss, pss_keyvalues in d_eid_pss_data.items():
                # logger.debug(f"{eid_pss}\t{pss_keyvalues}")
                # _, orig_hmr_rel_path, pss_scale_float, pss_rendercolor, pss_skin, _ = pss_keyvalues
                # logger.debug(f"\t{orig_hmr_rel_path}\t{pss_scale_float}\t{pss_rendercolor}\t{pss_skin}\n")
            '''
        else:
            logger.critical(f"ERROR! Can't read {self.vmf_file_name}.vmf to get entities!")
        
        # Сканирование файлов проекта
        self.scan_project_files()
        if not self.project_files_index:
            logger.critical(f'Unable to get project files!')
            return 1
        
        # Попробуем генерировать страшные ключи
        # {orig_hmr_rel_path}_{scale_percent}_{pss_skin}_{pss_rendercolor}
        '''
        scary_keys = []
        for eid_pss, pss_keyvalues in d_eid_pss_data.items():
            # Собираем страшный ключ
            _, orig_hmr_rel_path, pss_scale_float, pss_rendercolor, pss_skin, pss_origin = pss_keyvalues
            scale_percent = int(float(pss_scale_float) * 100)
            scary_key = f"{orig_hmr_rel_path}_{scale_percent}_{pss_skin}_{pss_rendercolor.replace(' ', '_')}"
            
            # Тут надо сохранить старое содержание locations_and_props для последующего сравнения
            
            # Заполняем новый locations_and_props
            if self.vmf_file_name not in self.project.locations_and_props:
                self.project.locations_and_props[self.vmf_file_name] = (set(), set())
            self.project.locations_and_props[self.vmf_file_name][0].add(scary_key)
            self.project.locations_and_props[self.vmf_file_name][1].add((eid_pss, scary_key))
            
            # Тут сравнивамем старое и новое содержание locations_and_props для данной локации.
            # Разница - набор ассетов, наличие которых надо проверить в других локациях.
            # Если исчезнувшие ассеты не используются на других локациях - их надо удалить.
            
            # Заполняем project_assets
            if orig_hmr_rel_path not in self.project.project_assets:
                orig_asset = OrigAsset(orig_hmr_rel_path, "orig_full_path", "orig_is_static", "orig_hash")
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
        # лучше нейминг
        for key_orig_hmr_rel_path, t_orig_pss in self.project.project_assets.items():
                orig_asset_obj, d_scary_pss = t_orig_pss
                logger.debug(f"orig_asset: {orig_asset}")
                for scary_key, psr_obj in dict.items():
                    logger.debug(f"\tkey: {key}")
                    logger.debug(f"\tpsr_obj: {psr_obj}\n")        
        '''
        
        # Удаление лишнего если remove_unused = 1
        '''
        1. Если включён режим удаления неиспользованного, удаляем все поскейленные ассеты проекта, находящиеся вне папки models/scaled (избавляемся от легаси моделей)
        2. Если включён режим удаления неиспользованного, проходимся по locations_and_props и составляем список вариаций ассетов, которые ни разу не встретились. Проходимся по файлам проекта и удаляем их.
        '''
        if self.args.remove_unused == 1:
            logger.warning(f'Warning! "remove_unused" mode is active!')
            logger.info(f"Cached maps will be checked and unused scaled assets will be deleted from the project files.")
            logger.info(f'Legacy-located scaled props (not from "models/scaled" folder) also will be deleted.')
            logger.error(f'Дописать эту ветку')
        
        # Создаём новый locations_and_props если локация компилится первый раз
        if self.vmf_file_name not in self.project.locations_and_props:
            self.project.locations_and_props[self.vmf_file_name] = (set(), set())
        
        # Предупреждающее сообщение для режима с проверкой хэш суммы оригинальных моделей
        if self.args.check_origs == 1:
            logger.warning(f'Warning! "check_origs" mode is active!')
            logger.warning(f'Hash-sum of all the original models of this map will be checked.')
            logger.warning(f'If its not same as before for a specific original model, all its scaled versions will be recompiled, because original asset has changed since last time we checked.')
            logger.warning(f"This increases the program's running time, so you can turn it off for fast presets of map compilation.")
            # тут продолжить логику
            # внутри следующего блока нельзя, там оригиналы повторяются
        
        # Собираем словарь того, что отсутствует в кэше и что нам надо рекомпилировать.
        # Также заполняем locations_and_props.
        d_orig_setvalues_todo = {}
        for eid_pss, t_pss_keyvalues in d_eid_pss_data.items():
            # Извлекаем инфу каждой энтити по айдишникам (могут быть повторяющиеся)
            pss_class, orig_hmr_rel_path, pss_scale_float, pss_rendercolor, pss_skin, pss_origin = t_pss_keyvalues
            
            # Вычисляем скейл в процентах и страшный ключ для каждого сочетания важных для нас параметров,
            # добавляем страшный ключ в locations_and_props
            scale_percent = int(float(pss_scale_float) * 100)
            scary_key = f"{orig_hmr_rel_path}_{scale_percent}_{pss_skin}_{pss_rendercolor.replace(' ', '_')}"
            self.project.locations_and_props[self.vmf_file_name][0].add(scary_key)
            self.project.locations_and_props[self.vmf_file_name][1].add((eid_pss, scary_key))
            
            # Генерируем кортеж из той инфы, которая нужна для перекомпила (без origin), т.е. для d_orig_setvalues_todo
            t_pss_keyvalues_noloc = (pss_class, orig_hmr_rel_path, pss_scale_float, pss_rendercolor, pss_skin)
            
            # Если режим форс рекомпайла - все вариации всех ориг ассетов добавляются в d_orig_setvalues_todo
            if self.args.force_recompile == 1:
                d_orig_setvalues_todo.setdefault(orig_hmr_rel_path, set()).add(t_pss_keyvalues_noloc)
                continue
            
            # Если оригинальной модели нет в project_assets - добавляем в d_orig_setvalues_todo
            if orig_hmr_rel_path not in self.project.project_assets:
                d_orig_setvalues_todo.setdefault(orig_hmr_rel_path, set()).add(t_pss_keyvalues_noloc)
                continue
            
            # Если оригинальная модель есть - срабатывает эта ветка
            logger.warning(f'Если оригинальная модель есть в project_assets - срабатывает эта ветка. Модель: "{orig_hmr_rel_path}"')
            # Получаем кортеж из keyvalues энтити по ключу (хаммер пути ориг модели)
            t_orig_pss = self.project.project_assets[orig_hmr_rel_path]
            # Получаем из кортежа объект ориг модели и словарь вариаций этой модели
            orig_asset_obj, d_scary_pss = t_orig_pss
            
            # Если страшного ключа нету среди ключей в словаре с вариациями - значит именно такой вариации мы ещё не делали, добавляем t_pss_keyvalues_noloc в d_orig_setvalues_todo
            if scary_key not in d_scary_pss:
                d_orig_setvalues_todo.setdefault(orig_hmr_rel_path, set()).add(t_pss_keyvalues_noloc)
                continue
        logger.info(f"{len(d_orig_setvalues_todo)} original assets need to be found and decompiled")
        variations_count = 0
        # logger.debug(f"d_orig_setvalues_todo:")
        for orig_hmr_rel_path, s_pss_keyvalues in d_orig_setvalues_todo.items():
            # logger.debug(f"\t{orig_hmr_rel_path}")
            for i, t_pss_keyvalues_noloc in enumerate(s_pss_keyvalues):
                variations_count += 1
                # logger.debug(f"\t\t{t_pss_keyvalues_noloc}")
        logger.info(f"{variations_count} variations of the original assets need to be created")
        
        # Защита от удалённых не нашей программой поскейленных ассетов.
        '''
        Если включён режим доп проверки потерянных ассетов, то для всех ориг ассетов уровня проходимся по версиям ассетов и проверяем что они действительно есть в проекте.
        Если какой-то вариации нету - добавляем в очередь на компиляцию.
        '''
        
        # Создаём папку для временного контента если её нет
        if not os.path.exists(TEMP_FILES_FOLDER):
            os.makedirs(TEMP_FILES_FOLDER)
        
        # Заполнение информации об ориг ассетах
        logger.info(f'Searching for original assets...')
        found_project_count = 0
        found_vpk_count = 0
        found_cache_count = 0
        not_found_count = 0
        d_not_found_items = {}
        progress = 0
        for orig_hmr_rel_path, s_pss_keyvalues in d_orig_setvalues_todo.items():
            logger.debug(f"Searching orig model: {orig_hmr_rel_path}")
            
            orig_full_path = None
            
            # Если оригинальная моделька есть в project_assets - мы можем узнать фулл путь из кэша
            if orig_hmr_rel_path in self.project.project_assets:
                t_orig_pss = self.project.project_assets[orig_hmr_rel_path]
                # Получаем из кортежа объект ориг модели и словарь вариаций этой модели
                orig_asset_obj, d_scary_pss = t_orig_pss
                logger.debug(f"orig_asset_obj: {orig_asset_obj}")
                logger.debug(f"d_scary_pss: {d_scary_pss}")
                orig_full_path = orig_asset_obj.orig_full_path # вот тут падает, почему-то orig_asset_obj может оказаться пустым
                if orig_full_path:
                    logger.debug(f"orig_full_path: {orig_full_path}")
                    found_cache_count += 1
                    continue
                else:
                    logger.warning(f'Warning! "{orig_hmr_rel_path}" found in cache, but full path for it does not found.')
            
            # Проходимся по searchpaths из гейминфо
            for t_searchpath in self.searchpaths:
                path_type, searchpath = t_searchpath
                
                orig_full_path = None
                
                if path_type == "folder_materials": continue
                
                if path_type == "folder_models":
                    orig_full_path = self.find_file_in_project(orig_hmr_rel_path, searchpath)
                    # logger.debug(f"searchpath: {searchpath}")
                    if orig_full_path:
                        logger.debug(f"orig_full_path: {orig_full_path}")
                        
                        orig_asset = self.build_orig_asset(orig_hmr_rel_path, orig_full_path)
                        
                        if not orig_asset:
                            logger.error(f'''Error! Can't build orig asset for "{orig_hmr_rel_path}". Full path: "{orig_full_path}"''')
                            not_found_count += 1
                            d_not_found_items[orig_hmr_rel_path] = s_pss_keyvalues
                            break
                        
                        # Добавляем в project_assets ориг модель, пока что с пустым словарём вариаций
                        self.project.project_assets[orig_hmr_rel_path] = (orig_asset, {})
                        
                        found_project_count += 1
                        break
                
                if path_type == "vpk":
                    if "_materials_" in searchpath: continue
                    orig_full_path = self.find_file_in_vpk(orig_hmr_rel_path, searchpath)
                    if orig_full_path:
                        logger.debug(f"orig_full_path: {orig_full_path}")
                        
                        # тут надо вытаскивать ориг модели и все материалы во временную папку !!!
                        
                        found_vpk_count += 1
                        break
                
            if not orig_full_path:
                not_found_count += 1
                d_not_found_items[orig_hmr_rel_path] = s_pss_keyvalues
            
            progress += 1
            if progress >= len(d_orig_setvalues_todo):
                print(f"Progress: Done!")
            else:
                print(f"Progress: {int(progress*100/len(d_orig_setvalues_todo))}%", end="\r")
            
        '''
        # logger.debug(f" ")
        # logger.debug(f"project_assets:")
        # for project_asset in self.project.project_assets.items():
            # logger.debug(f"{project_asset}")
        # logger.debug(f"{self.project.project_assets}")
        # self.cache.save()
        # logger.debug(f" ")
        '''
        
        logger.info(f"From {len(d_orig_setvalues_todo)} original assets:")
        logger.info(f"{found_cache_count} found in cache")
        logger.info(f"{found_project_count} found in project files")
        logger.info(f"{found_vpk_count} found in VPK files")
        if not_found_count != 0:
            logger.error(f"{not_found_count} original assets not found:")
            for orig_hmr_rel_path, s_pss_keyvalues in d_not_found_items.items():
                logger.error(f'"{orig_hmr_rel_path}"')
        # logger.info(f"{len(need_to_find_in_vpks)} original assets need to be searched for in VPKs")
        elapsed_time = time.time() - start_time
        hours, remainder = divmod(elapsed_time, 3600)
        minutes, seconds = divmod(remainder, 60)
        logger.info(f"Time spent: {int(hours)} hours, {int(minutes)} minutes, {seconds:.2f} seconds\n")
        input(f"edik_krutoi")
        
        '''        
                # - [покраска] вычисляем всякую хуйню по сочетаниям скинов и материалов, редактируем QC оригинала
                
                # - [покраска] находим ориг материалы, создаём копии VMT в соответствии с вычислениями, кладём в нужное место
                
                # - [скейлинг] копируем QC для каждой вариации скейла и меняем параметры (скейл для разных типов, статик проп если был динамик и тд)
                
                # - компилируем оригинал и все вариации
                
                # - проверяем что всё доехало куда надо, заполняем данные о получившихся приколах в project_assets
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

    def scan_project_files(self):
        logger.info(f'Scanning project files...')
        self.project_files_index.clear()
        base_path = self.args.game
        for root, dirs, files in os.walk(base_path, topdown=True):
            if os.path.abspath(root) == os.path.abspath(base_path): # 2532
                dirs[:] = [d for d in dirs if d not in SKIP_FOLDERS]
            # dirs[:] = [d for d in dirs if d not in SKIP_FOLDERS] # 2439
            rel_dir = os.path.relpath(root, base_path).lower().replace('\\', '/')
            self.project_files_index[rel_dir] = files

    def find_file_in_project(self, rel_path, searchpath):
        base_path = self.args.game.lower().replace('\\', '/')
        rel_path = rel_path.lower().replace('\\', '/')
        
        # Быстрая проверка по ключу
        ending = "/".join(os.path.dirname(rel_path).split("/")[1:]) if len(os.path.dirname(rel_path).split("/")) >1 else ''
        key_path = searchpath.replace(base_path, '')[1:] + '/' + ending
        if not key_path in self.project_files_index: return None
        files = self.project_files_index[key_path]
        if not files: return None
        # logger.debug(f'files: {files}')
        if os.path.basename(rel_path) in files:
            abs_path = base_path + '/' + key_path + '/' + os.path.basename(rel_path)
            # logger.debug(f'abs_path" {abs_path}')
            # logger.debug(f'os.path.exists(abs_path)" {os.path.exists(abs_path)}')
            if os.path.exists(abs_path): return abs_path
        
        return None
        
        # Старый способ
        logger.warning(f'find_file_in_project old logic! rel_path: {rel_path}')
        for rel_dir, files in self.project_files_index.items():
            # logger.debug(f'rel_dir: "{rel_dir}"')
            # Формируем полный путь папки
            abs_dir = os.path.join(base_path, rel_dir).replace('\\', '/') if rel_dir != "." else base_path
            
            # Проверяем условие начала пути
            if not abs_dir.startswith(searchpath): continue
            
            # Проверяем все файлы по концовке
            for f in files:
                abs_path = os.path.join(abs_dir, f).replace('\\', '/')
                if abs_path.endswith(rel_path):
                    return abs_path

    def build_vpk_trees(self):
        self.d_vpk_trees = {}
        for t_searchpath in self.searchpaths:
            path_type, vpk_path = t_searchpath
            if path_type != "vpk": continue
            vpk_tree = subprocess.run(
                [self.vpkeditcli_path, '--file-tree', vpk_path],
                check=True,
                text=True,
                capture_output=True
            )
            vpk_tree_out = vpk_tree.stdout.lower()
            self.d_vpk_trees[vpk_path] = vpk_tree_out

    def find_file_in_vpk(self, rel_path, vpk_path):
        if not self.d_vpk_trees:
            logger.error(f'ERROR! Something wrong with VPK reading logic! Please report about this bug!')
            return None
        orig_basename = os.path.basename(rel_path)
        vpk_tree_out = self.d_vpk_trees[vpk_path]
        if orig_basename in vpk_tree_out: return vpk_path + '/' + rel_path
        return None

    def build_orig_asset(self, orig_hmr_rel_path, orig_full_path):
        logger.debug(f'build_orig_asset start')
        # logger.debug(f'orig_hmr_rel_path: {orig_hmr_rel_path}')
        # logger.debug(f'orig_full_path: {orig_full_path}')
        
        # Проверочка на вшивость пути
        if ".vpk/" in orig_full_path:
            logger.debug(f'".vpk/" in orig_full_path')
            # тут нужна логика чтобы открывать впк и читать мдл 
            # ЛИБО
            # ошибка если мы всегда ожидаем здесь подготовленный путь с вытащенным оригиналом из впк
            # я думаю что второй вариант будет лучше
            return None
        
        # Проверяем является ли оригинал статик пропом
        with open(orig_full_path, 'rb') as f:
            # Проверяем сигнатуру и версию мдл файла
            hdr = f.read(8)  # int id; int version;
            if len(hdr) < 8:
                logger.error(f"Error! The file is too short for the MDL header: {orig_full_path}")
                return None
            mdl_id, version = struct.unpack('<II', hdr)
            if mdl_id != MDL_IDST_LE:
                logger.error(f"Error! Doesn't look like Source MDL (IDST was expected): {orig_full_path}")
                return None
            # В исходниках studio.h поле flags идёт после 6 векторов (смещение 0x98)
            f.seek(MDL_FLAGS_OFFSET)
            data = f.read(4)
            if len(data) < 4:
                logger.error(f"Error! Failed to read the flags field: {orig_full_path}")
                return None
            (flags,) = struct.unpack('<I', data)
        orig_is_static = (flags & MDL_STATIC_PROP_FLAG) != 0
        # logger.debug(f'orig_is_static: {orig_is_static}')
        
        # Читаем хэш оригинала
        orig_hash = get_hash_of_file(orig_full_path)
        # logger.debug(f'orig_hash: {orig_hash}')
        
        # Функции для чтения orig_cdmaterials
        def read_u32(f) -> int:
            d = f.read(4)
            if len(d) < 4:
                logger.error(f"Error! Unexpected EOF while reading <I>. MDL: {orig_full_path}")
                return None
            return struct.unpack('<I', d)[0]
        def read_i32_at(f, off: int) -> int:
            f.seek(off); return read_u32(f)
        def read_cstring_at(f, off: int, limit: int = 1024) -> str:
            f.seek(off)
            out = bytearray()
            for _ in range(limit):
                b = f.read(1)
                if not b or b == b'\x00': break
                out += b
            return out.decode('ascii', errors='ignore')
        
        # Читаем orig_cdmaterials
        with open(orig_full_path, 'rb') as f:
            f.seek(0)
            if f.read(4) != MDL_IDST:
                logger.error(f"Error! Doesn't look like Source MDL (IDST was expected): {orig_full_path}")
                return None
            version = read_u32(f)
            if not version: return None

            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            if file_size < MDL_STUDIOHDR_SIZE_MIN:
                logger.error(f"Error! MDL too short (broken header): {orig_full_path}")
                return None

            # Читаем приколы
            texture_count = read_i32_at(f, MDL_OFF_TEXTURE_COUNT)
            texture_index = read_i32_at(f, MDL_OFF_TEXTURE_INDEX)
            txdir_count   = read_i32_at(f, MDL_OFF_TXDIR_COUNT)
            txdir_index   = read_i32_at(f, MDL_OFF_TXDIR_INDEX)
            skinref_count = read_i32_at(f, MDL_OFF_SKINREF_COUNT)
            skinfam_count = read_i32_at(f, MDL_OFF_SKINFAM_COUNT)
            skin_index    = read_i32_at(f, MDL_OFF_SKIN_INDEX)
            
            # Проверка наличия на всякий случай
            if any(v is None for v in (
                texture_count, texture_index,
                txdir_count, txdir_index,
                skinref_count, skinfam_count,
                skin_index
            )):
                logger.error(f"Error reading materials info from MDL: {orig_full_path}")
                return None

            # Доп проверка значения на всякий случай
            for name, val in (("textureindex", texture_index), ("cdtextureindex", txdir_index), ("skinindex", skin_index)):
                if not (0 <= val < file_size):
                    logger.error(f'Error! Incorrect "{name}" ({val})" in MDL: {orig_full_path}')
                    return None

            # И ещё одна проверочка
            if texture_count <= 0 or skinref_count <= 0 or skinfam_count <= 0:
                # У моделей без "вариантов" skinfam_count обычно 1
                logger.error(f'Error! Incorrect table sizes (values > 0 were expected). MDL: {orig_full_path}')
                return None

            # return (version, texture_count, texture_index, txdir_count, txdir_index, skinref_count, skinfam_count, skin_index)
            
            # перемычка
            # (version, tcnt, toff, txdcnt, txdoff, refcnt, famcnt, skoff) = (version, texture_count, texture_index, txdir_count, txdir_index, skinref_count, skinfam_count, skin_index)
        
            # Получаем список имён материалов
            materials_names: List[str] = []
            for i in range(texture_count):
                entry_off = texture_index + i * MDL_MSTEXTURE_SIZE
                f.seek(entry_off + MDL_OFF_MSTEX_NAMEOFF)
                (name_rel,) = struct.unpack('<i', f.read(4))
                name_abs = entry_off + name_rel
                materials_names.append(read_cstring_at(f, name_abs))
            
            # logger.debug(f'materials_names: {materials_names}')
            
            # Читаем индексы для orig_skinfamilies (сырая таблица большого размера)
            f.seek(skin_index)
            total = skinfam_count * skinref_count
            raw = f.read(total * 2)
            if len(raw) < total * 2:
                logger.error(f'Error reading skin-table! MDL: {orig_full_path}')
                return None
            idxs = struct.unpack('<' + 'H'*total, raw)
            
            # Преобразуем индексы в имена (полная матрица numskinfamilies x numskinref), собираем orig_skinfamilies
            orig_skinfamilies: List[List[str]] = []
            for r in range(skinfam_count):
                row: List[str] = []
                base = r * skinref_count
                for c in range(skinref_count):
                    t = idxs[base + c]
                    name = materials_names[t] if 0 <= t < len(materials_names) else ''
                    row.append(name)
                orig_skinfamilies.append(row)
            
            # logger.debug(f'orig_skinfamilies raw: {orig_skinfamilies}')
            
            # Ищем изменяемые колонки, где значения различаются между скинами
            varying_cols: List[int] = []
            if orig_skinfamilies:
                cols = len(orig_skinfamilies[0])
                for c in range(cols):
                    values = {row[c] for row in orig_skinfamilies}
                    if len(values) > 1:
                        varying_cols.append(c)

            # Если изменяемых колонок нет, возвращаем пустые списки на каждую строку, чтобы было корректно по типу
            if not varying_cols:
                # return [[] for _ in range(skinfam_count)]
                # orig_skinfamilies = [[] for _ in range(skinfam_count)]
                pass # попробуем
            else:
                # Собираем QC формат таблицы (только изменяемые колонки, имена материалов)
                qc_fams: List[List[str]] = []
                for row in orig_skinfamilies:
                    qc_row: List[str] = []
                    for c in varying_cols:
                        name = row[c] or ''
                        # Добавим .vmt, если его нет в имени
                        if name and not name.lower().endswith('.vmt'):
                            name = f"{name}.vmt"
                        qc_row.append(name)
                    qc_fams.append(qc_row)
                # return qc_fams
                orig_skinfamilies = qc_fams
            
            # logger.debug(f' ')
            logger.debug(f'orig_skinfamilies: {orig_skinfamilies}')            
            
            # (version, texture_count, texture_index, txdir_count, txdir_index, skinref_count, skinfam_count, skin_index)
            
            # Читаем cdmaterials
            f.seek(txdir_index)
            offs = struct.unpack('<' + 'i'*txdir_count, f.read(4*txdir_count))
            paths: List[str] = []
            for off in offs:
                s = read_cstring_at(f, off) if off != 0 else ""
                # нормализуем: без ведущего/замыкающего слэша, без префикса materials/
                s = s.replace('\\', '/').removeprefix('materials/').strip('/')
                paths.append(s)  # пустая строка допустима (корень materials)
            # уберём дубликаты с сохранением порядка
            seen = set(); orig_cdmaterials = []
            for p in paths:
                if p not in seen:
                    seen.add(p); orig_cdmaterials.append(p)
            # return orig_cdmaterials
            
            # logger.debug(f' ')
            # logger.debug(f'orig_cdmaterials: {orig_cdmaterials}')
            # logger.debug(f' ')
            
        # Конструируем множество orig_materials, зная orig_cdmaterials и materials_names
        orig_materials = set()
        for cdmat_rel_folder in orig_cdmaterials:
            # logger.debug(f'self.gameinfo_folder_path: {self.gameinfo_folder_path}')
            # logger.debug(f'cdmat_rel_folder: {cdmat_rel_folder}')
            # logger.debug(f'orig_full_path: {orig_full_path}')
            
            cdmat_abs_folder = self.gameinfo_folder_path + 'materials/' + cdmat_rel_folder
            cdmat_abs_folder = cdmat_abs_folder.lower()
            # logger.debug(f'cdmat_abs_folder: {cdmat_abs_folder}')
            
            for mat_name in materials_names:
                mat_name = mat_name.lower()
                check_mat_path = cdmat_abs_folder + '/' + mat_name + '.vmt'
                if os.path.exists(check_mat_path):
                    logger.debug(f'material found: {check_mat_path}')
                    rel_mat_path = check_mat_path.split("materials/", 1)[1]
                    # logger.debug(f'rel path: {rel_mat_path}')
                    orig_materials.add(rel_mat_path)
                else: logger.debug(f'material not found: {mat_name}')
        
        # logger.debug(f' ')
        # logger.debug(f'orig_materials: {orig_materials}')
        
        # Конструируем orig_skin_map
        
        
        orig_skin_map = {} # создаём пустой, потому что ещё ни разу не красили
        '''
        orig_skin_map: Dict[Tuple[int, str], int] = field(default_factory=dict) # словарь-карта ремапа: ориг скин + цвет -> новый скин
        '''
        
        orig_asset = OrigAsset(orig_hmr_rel_path, orig_full_path, orig_is_static, orig_hash, orig_cdmaterials, orig_skinfamilies, orig_materials, orig_skin_map)
        
        return orig_asset

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
    logger.info(f"Extracting searchpaths from Gameinfo.txt")
    logger.debug(f"get_searchpaths gameinfo_path: {gameinfo_path}")
    
    # |all_source_engine_paths|
    all_source_engine_paths = os.path.abspath(os.path.join(get_script_path(), "../../half-life 2"))
    all_source_engine_paths = str(Path(all_source_engine_paths).as_posix()).lower()+"/"
    logger.debug(f"all_source_engine_paths: {all_source_engine_paths}")
    ssdkb13sp_path = os.path.join(all_source_engine_paths, "../source sdk base 2013 singleplayer")
    
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
                elif os.path.exists(ssdkb13sp_path + "/" + path):
                    logger.debug(f'VPK found: {ssdkb13sp_path + "/" + path}')
                    searchpaths.append((path_type, ssdkb13sp_path + "/" + path))
                elif os.path.exists(ssdkb13sp_path + "/" + path.replace(".vpk", "_dir.vpk")):
                    logger.debug(f'VPK found: {ssdkb13sp_path + "/" + path.replace(".vpk", "_dir.vpk")}')
                    searchpaths.append((path_type, ssdkb13sp_path + "/" + path.replace(".vpk", "_dir.vpk")))
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
    logger.info(f"Extracting entities data from VMF...")
    results = {}
    
    def skip_block(lines_iter):
        '''
        Пропускает блок вида:
        name
        {
            ...
        }
        Вызывается уже после чтения строки с именем блока (например, 'hidden').
        '''
        depth = 0
        for line in lines_iter:
            s = line.strip()
            if s == "{":
                depth += 1
                continue
            if s == "}":
                depth -= 1
                if depth == 0:
                    break
                continue
            # всё остальное игнорируем, пока не закроем блок
    
    with open(vmf_path, 'r', encoding="cp1252") as f:
        lines = iter(f)
        for line in lines:
            line = line.strip()
            
            if line == "hidden":
                skip_block(lines)
                continue
            
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
                        
                        if not model:
                            logger.error(f'ERROR! Cant get model of this entity: ID {entity_id}. Origin: {origin}')
                            continue
                        
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
                            logger.error(f"ERROR! Multiple times scaled model unsed as non-scaled. Name: {os.path.basename(model)}. ID: {entity_id}. Origin: {origin}. Skipping!\nPlease replace it with the original model!")
                            continue
                        if "_scaled_" in model:
                            logger.warning(f"Warning! Scaled model unsed as non-scaled. Name: {os.path.basename(model)}. ID: {entity_id}. Origin: {origin}.\nPlease replace it with the original model!")
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

# Чтение хэша файла
def get_hash_of_file(filepath: str) -> str:
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

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
    logger.debug(f'\n\n========================================================================================\n=================================== NEW COMPILATION ====================================\n=========================== Start date: {time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(time.time()))} ============================\n========================================================================================')
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
