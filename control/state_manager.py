from enum import Enum, auto
from utils.logger import logger


class AppState(Enum):
    INITIALIZING = auto()
    READY        = auto()
    SCANNING     = auto()
    PAUSED       = auto()
    ERROR        = auto()
    SHUTDOWN     = auto()


VALID_TRANSITIONS = {
    AppState.INITIALIZING: [AppState.READY, AppState.ERROR],
    AppState.READY:        [AppState.SCANNING, AppState.SHUTDOWN],
    AppState.SCANNING:     [AppState.PAUSED, AppState.READY, AppState.ERROR, AppState.SHUTDOWN],
    AppState.PAUSED:       [AppState.SCANNING, AppState.READY, AppState.SHUTDOWN],
    AppState.ERROR:        [AppState.READY, AppState.SHUTDOWN],
    AppState.SHUTDOWN:     [],
}


class StateManager:
    def __init__(self):
        self._state = AppState.INITIALIZING

    def transition(self, new_state: AppState) -> bool:
        if new_state in VALID_TRANSITIONS.get(self._state, []):
            logger.info(f"Durum geçişi: {self._state.name} → {new_state.name}")
            self._state = new_state
            return True
        logger.warning(f"Geçersiz durum geçişi: {self._state.name} → {new_state.name}")
        return False

    @property
    def state(self) -> AppState:
        return self._state

    def is_scanning(self) -> bool:
        return self._state == AppState.SCANNING

    def is_ready(self) -> bool:
        return self._state == AppState.READY
