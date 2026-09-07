"""
qgroundcontrol_config.py

Helpers to write an RTK base station position into QGroundControl's
application settings file (QGroundControl.ini), so PyGPSClient can populate
the "Use Specified Base Position" fields in QGC's RTK GPS settings without
manual copy/paste.

QGroundControl stores its settings via Qt QSettings in INI format. The RTK
base station position lives in the ``[RTK]`` group with the following keys
(see mavlink/qgroundcontrol ``src/Settings/RTK.SettingsGroup.json``)::

    useFixedBasePosition         0 = Survey-In, 1 = use fixed position
    fixedBasePositionLatitude    decimal degrees
    fixedBasePositionLongitude   decimal degrees
    fixedBasePositionAltitude    vertical metres (WGS-84 ellipsoidal / HAE)
    fixedBasePositionAccuracy    metres

NB: QGroundControl caches its settings in memory and rewrites the whole file
on exit, so it must be CLOSED when these values are written and then (re)started
afterwards to pick up the new base position.

Created on 2 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

from configparser import ConfigParser
from os import getenv
from pathlib import Path
from platform import system

QGC_ORG = "QGroundControl.org"
QGC_INI = "QGroundControl.ini"
RTK_GROUP = "RTK"
KEY_USEFIXED = "useFixedBasePosition"
KEY_LAT = "fixedBasePositionLatitude"
KEY_LON = "fixedBasePositionLongitude"
KEY_ALT = "fixedBasePositionAltitude"
KEY_ACC = "fixedBasePositionAccuracy"


def default_qgc_ini_path() -> Path:
    """
    Return the platform default fully-qualified path to QGroundControl.ini.

    The file may not exist (e.g. if QGC has never been run), in which case
    it can be created by :func:`write_base_position`.

    :return: default path to QGroundControl.ini
    :rtype: Path
    """

    plat = system()
    if plat == "Windows":
        base = getenv("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(base) / QGC_ORG / QGC_INI
    # macOS and Linux: QGC forces IniFormat under the XDG config location
    base = getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / QGC_ORG / QGC_INI


def write_base_position(
    inifile: str,
    lat: float,
    lon: float,
    alt: float,
    accuracy: float = 0.0,
) -> Path:
    """
    Write an RTK fixed base station position into QGroundControl.ini.

    Existing settings in the file are preserved; only the RTK fixed base
    position keys are updated (and ``useFixedBasePosition`` is enabled). The
    ``[RTK]`` section (and the file itself) is created if it does not exist.

    :param str inifile: fully-qualified path to QGroundControl.ini
    :param float lat: latitude (decimal degrees)
    :param float lon: longitude (decimal degrees)
    :param float alt: altitude (metres, WGS-84 ellipsoidal / HAE)
    :param float accuracy: position accuracy estimate (metres)
    :return: path written
    :rtype: Path
    :raises ValueError: if lat/lon are out of range
    """

    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"Latitude {lat} out of range -90..90")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"Longitude {lon} out of range -180..180")

    path = Path(inifile)
    # interpolation=None because Qt values may legitimately contain '%'
    cfg = ConfigParser(interpolation=None)
    # preserve key case exactly as Qt writes it
    cfg.optionxform = str
    if path.exists():
        cfg.read(path, encoding="utf-8")

    if not cfg.has_section(RTK_GROUP):
        cfg.add_section(RTK_GROUP)

    cfg.set(RTK_GROUP, KEY_USEFIXED, "1")
    cfg.set(RTK_GROUP, KEY_LAT, f"{lat:.9f}")
    cfg.set(RTK_GROUP, KEY_LON, f"{lon:.9f}")
    cfg.set(RTK_GROUP, KEY_ALT, f"{alt:.3f}")
    cfg.set(RTK_GROUP, KEY_ACC, f"{accuracy:.3f}")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        # Qt QSettings ini uses no spaces around the '=' delimiter
        cfg.write(file, space_around_delimiters=False)

    return path
