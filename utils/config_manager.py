import configparser
import os


class ConfigManager:
    _instance = None

    def __new__(cls, config_path: str = "config.ini"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load(config_path)
        return cls._instance

    def _load(self, config_path: str):
        self._config = configparser.ConfigParser()
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full_path = os.path.join(base_dir, config_path)
        self._config.read(full_path, encoding="utf-8")
        self._base_dir = base_dir

    def get(self, section: str, key: str, fallback=None):
        return self._config.get(section, key, fallback=fallback)

    def get_int(self, section: str, key: str, fallback: int = 0) -> int:
        return self._config.getint(section, key, fallback=fallback)

    def get_float(self, section: str, key: str, fallback: float = 0.0) -> float:
        return self._config.getfloat(section, key, fallback=fallback)

    def get_path(self, section: str, key: str, fallback: str = "") -> str:
        rel = self._config.get(section, key, fallback=fallback)
        return os.path.join(self._base_dir, rel)

    def set(self, section: str, key: str, value: str):
        if not self._config.has_section(section):
            self._config.add_section(section)
        self._config.set(section, key, value)
