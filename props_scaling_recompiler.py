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
    orig_asset: OrigAsset                   # объект 
    pss_scale: float                        # скейл
    pss_skin: int                           # какой скин оригинальной модели используется для статик пропа
    pss_rendercolor: str = "255 255 255"    # цвет относительно оригинальной модели
    # Данные поскейленной модели, которые мы получаем на выходе
    scld_hmr_rel_path: str                  # хаммеровский путь поскейленной модели
    scld_skin: int                          # какой скин будет назначен поскейленной модели из-за покраски
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
        self.path = cache_file
        self.projects: Dict[str, Project] = {}

    def load(self) -> None:
        if self.path.exists():
            with self.path.open("rb") as f:
                self.projects = pickle.load(f)
        else:
            self.projects = {}

    def save(self) -> None:
        with self.path.open("wb") as f:
            pickle.dump(self.projects, f)

    def get_project(self, gameinfo_path: str) -> Project: # *
        p = self.projects.get(gameinfo_path) # есть ли у нас позиция в словаре с ключом gameinfo_path?
        if not p:
            p = Project(gameinfo_path=gameinfo_path) # если нету такого, то создаём новый экземпляр класса проект, назначаем ему путь гейминфо
            self.projects[gameinfo_path] = p # по ключу gameinfo_path добавляем новый проект
        return p
        # * - возможно стоит прям здесь входящий гейминфо путь обрабатывать так:
        # str(Path(gameinfo_path).as_posix()).lower()
        # но думаю что лучше там, где будет вызываться класс GlobalCache и этот метод get_project

# Тут будут другие классы

# ----------------------------------------
#   Функции
# ----------------------------------------

# Сетап логгера
def setup_logging():
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Хэндлер консоли
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(fmt))
    root.addHandler(ch)

    # Хэндлер в файл (и ротация, чтобы не разрастался)
    fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt))
    root.addHandler(fh)

# Функция для удобных пауз в консоли
def pause_if_needed(on_error: bool):
    # Пауза только в интерактивной консоли (например, двойной клик в Windows).
    # Управляется переменной окружения PAUSE_ON_EXIT='1'.
    if os.environ.get("PAUSE_ON_EXIT", "0") == "1" and sys.stdin.isatty():
        msg = "Press Enter to exit..." if not on_error else "Error occurred. Press Enter to exit..."
        try:
            input(msg)
        except EOFError:
            pass

# ----------------------------------------
#   Мейн функция
# ----------------------------------------
def main() -> int:
    logging.info("Started")
    # тут всякая логика
    return 0

# ----------------------------------------
#   Концовочка с подхватами
# ----------------------------------------
if __name__ == "__main__":
    setup_logging()
    exit_code = 0
    try:
        exit_code = int(main())
    except KeyboardInterrupt:
        logging.info("Interrupted by user (Ctrl+C)")
        exit_code = 130
        pause_if_needed(on_error=False)
    except Exception:
        logging.exception("Unhandled exception")
        exit_code = 1
        pause_if_needed(on_error=True)
    finally:
        logging.shutdown()
    sys.exit(exit_code)
