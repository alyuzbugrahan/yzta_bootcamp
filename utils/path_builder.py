import os
from datetime import datetime
from utils.config_manager import ConfigManager


class PathBuilder:
    def __init__(self):
        cfg = ConfigManager()
        self._images_dir = cfg.get_path("storage", "images_dir", "data/images")
        self._exports_dir = cfg.get_path("storage", "exports_dir", "data/exports")

    def get_image_path(self, batch_id: str, fig_id: int, result: str) -> str:
        date_str = datetime.now().strftime("%Y-%m-%d")
        folder = os.path.join(self._images_dir, date_str, batch_id)
        os.makedirs(folder, exist_ok=True)
        filename = f"Fig_{fig_id:04d}_{result}.jpg"
        return os.path.join(folder, filename)

    def get_export_path(self, batch_id: str) -> str:
        os.makedirs(self._exports_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self._exports_dir, f"{batch_id}_{timestamp}.csv")
