import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    root: Path
    config_dir: Path
    data_dir: Path
    log_dir: Path
    dashboard_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "store.sqlite3"

    @property
    def secrets_file(self) -> Path:
        return self.config_dir / "secrets.json"

    def ensure(self) -> None:
        for d in (self.root, self.config_dir, self.data_dir, self.log_dir, self.dashboard_dir):
            Path(d).mkdir(parents=True, exist_ok=True)


def project_root() -> Path:
    """Project root. Defaults to /opt/airrelay on the Pi, override via AIRRELAY_HOME."""
    env = os.environ.get("AIRRELAY_HOME")
    if env:
        return Path(env).resolve()
    candidate = Path(__file__).resolve().parents[3]  # .../src/airrelay/util -> project root
    if (candidate / "src" / "airrelay").exists():
        return candidate
    return Path("/opt/airrelay")


def resolve_paths(root: Path | str | None = None) -> Paths:
    root = Path(root) if root else project_root()
    return Paths(
        root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        log_dir=root / "logs",
        dashboard_dir=root / "dashboard",
    )
