import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# ----------------------------------------
#   Основные константы
# ----------------------------------------
LOG_FILE = f"{os.path.splitext(os.path.basename(sys.argv[0]))[0]}_log.txt"

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
