# backend/app/utils.py
import json
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

class Utils:
    @staticmethod
    def human_size(n: int | None) -> str:
        """以十進位(1000)換算：B, KB, MB, GB, TB, PB"""
        try:
            v = int(n)
        except (TypeError, ValueError):
            return "-"
        if v < 0:
            return "-"
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        val = float(v)
        i = 0
        while val >= 1000.0 and i < len(units) - 1:
            val /= 1000.0
            i += 1
        # 小數一位，去掉無意義的 .0
        num = f"{val:.1f}".rstrip("0").rstrip(".")
        return f"{num} {units[i]}"

    @staticmethod
    def setup_devtools_static(wk_dir: Path, project_root: Path) -> None:
        """DevTools helper"""
        wk_dir.mkdir(parents=True, exist_ok=True)       # ← 加 parents=True

        path = wk_dir / "com.chrome.devtools.json"
        if not path.exists():
            payload = {
                "workspace": {
                    "root": str(project_root).replace("\\", "/"),
                    "uuid": "6ec0bd7f-11c0-43da-975e-2a8ad9ebae0b",
                }
            }
            path.write_text(                           # ← 先序列化為字串
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        return None