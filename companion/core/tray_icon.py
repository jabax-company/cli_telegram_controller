"""Windows tray icon controller."""

from __future__ import annotations

import logging
import platform
import threading
from dataclasses import dataclass

from companion.core.runtime_control import request_stop

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional GUI dependency
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    pystray = None
    Image = None
    ImageDraw = None


def _build_icon_image():
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, 58, 58), fill=(40, 167, 69, 255), outline=(20, 96, 45, 255), width=4)
    draw.rectangle((28, 20, 36, 44), fill=(255, 255, 255, 255))
    draw.ellipse((28, 46, 36, 54), fill=(255, 255, 255, 255))
    return image


@dataclass
class TrayController:
    icon: object
    thread: threading.Thread

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)


def start_tray_icon() -> TrayController | None:
    if platform.system().lower() != "windows":
        logger.info("Tray icon disabled: only enabled on Windows.")
        return None
    if pystray is None or Image is None or ImageDraw is None:
        logger.warning(
            "Tray icon disabled: install optional deps `pystray` and `Pillow`."
        )
        return None

    title = "Claude Code Bot activo"
    icon_image = _build_icon_image()
    icon = pystray.Icon(
        "claude_code_bot",
        icon_image,
        title=title,
        menu=pystray.Menu(
            pystray.MenuItem("Estado: Activo", None, enabled=False),
            pystray.MenuItem(
                "Stop",
                lambda _icon, _item: request_stop("tray_stop"),
            ),
        ),
    )

    thread = threading.Thread(target=icon.run, name="tray-icon", daemon=True)
    thread.start()
    return TrayController(icon=icon, thread=thread)
