"""VRGB Suite: sun position, location suggestion and Suite settings.

Everything here is Suite-only; the Core config file just carries these keys
(Core's save_config keeps keys it does not know).
"""

import datetime as _dt
import math
import os

# Suite settings stored next to Core's keys in ~/.config/vrgb/config.json
DEFAULTS = {
    "idle_enabled": True,
    "idle_timeout_seconds": 12,
    "day_off_enabled": False,
    "latitude": None,
    "longitude": None,
    "day_forced_off": False,
}


def settings(cfg):
    """Validated copy of the Suite settings found in a Core config dict."""
    s = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    for key in ("idle_enabled", "day_off_enabled", "day_forced_off"):
        s[key] = bool(s[key])
    try:
        s["idle_timeout_seconds"] = max(1, min(600, int(s["idle_timeout_seconds"])))
    except (TypeError, ValueError):
        s["idle_timeout_seconds"] = DEFAULTS["idle_timeout_seconds"]
    try:
        s["latitude"] = max(-90.0, min(90.0, float(s["latitude"])))
        s["longitude"] = max(-180.0, min(180.0, float(s["longitude"])))
    except (TypeError, ValueError):
        s["latitude"] = s["longitude"] = None
    return s


def _sun_event_utc(date, lat, lon, rising, zenith=90.833):
    """UTC datetime of sunrise/sunset on `date` (Almanac for Computers algorithm).

    Returns True if the sun never sets that day (polar day), False if it never
    rises (polar night). Accuracy is ~1-2 minutes, plenty for a backlight.
    """
    rad, deg = math.radians, math.degrees
    n = date.timetuple().tm_yday
    lng_hour = lon / 15.0
    t = n + ((6 if rising else 18) - lng_hour) / 24.0
    m = 0.9856 * t - 3.289
    l = (m + 1.916 * math.sin(rad(m)) + 0.020 * math.sin(rad(2 * m)) + 282.634) % 360
    ra = deg(math.atan(0.91764 * math.tan(rad(l)))) % 360
    ra += (l // 90) * 90 - (ra // 90) * 90
    ra /= 15.0
    sin_dec = 0.39782 * math.sin(rad(l))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(rad(zenith)) - sin_dec * math.sin(rad(lat))) / (cos_dec * math.cos(rad(lat)))
    if cos_h > 1:
        return False
    if cos_h < -1:
        return True
    h = deg(math.acos(cos_h))
    h = (360 - h if rising else h) / 15.0
    ut = (h + ra - 0.06571 * t - 6.622 - lng_hour) % 24
    midnight = _dt.datetime(date.year, date.month, date.day, tzinfo=_dt.timezone.utc)
    return midnight + _dt.timedelta(hours=ut)


def sun_times(lat, lon, date=None):
    """(sunrise, sunset) as local-time datetimes, or a bool for polar day/night."""
    date = date or _dt.date.today()
    rise = _sun_event_utc(date, lat, lon, True)
    sett = _sun_event_utc(date, lat, lon, False)
    if isinstance(rise, bool) or isinstance(sett, bool):
        return rise if isinstance(rise, bool) else sett
    if sett < rise:
        sett += _dt.timedelta(days=1)
    return rise.astimezone(), sett.astimezone()


def is_daytime(lat, lon, now=None):
    now = now or _dt.datetime.now().astimezone()
    times = sun_times(lat, lon, now.date())
    if isinstance(times, bool):
        return times
    rise, sett = times
    return rise <= now < sett


def local_timezone_name():
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz and "/" in tz and not tz.startswith("/"):
        return tz
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return None
    marker = "/zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else None


def _parse_iso6709(coord):
    """'+5215+02100' / '+405042-0735258' -> (lat, lon) in degrees."""
    split = max(coord.rfind("+"), coord.rfind("-"))
    out = []
    for part, deg_digits in ((coord[:split], 2), (coord[split:], 3)):
        sign = -1 if part[0] == "-" else 1
        digits = part[1:]
        d = int(digits[:deg_digits])
        mnt = int(digits[deg_digits:deg_digits + 2] or 0)
        sec = int(digits[deg_digits + 2:] or 0)
        out.append(sign * (d + mnt / 60 + sec / 3600))
    return round(out[0], 4), round(out[1], 4)


def guess_location():
    """Suggest (lat, lon, label) from the system timezone — offline and free.

    Uses the reference city of the tz database zone (e.g. Europe/Warsaw -> Warsaw).
    Returns None if the zone cannot be resolved.
    """
    tz = local_timezone_name()
    if not tz:
        return None
    for tab in ("/usr/share/zoneinfo/zone1970.tab", "/usr/share/zoneinfo/zone.tab"):
        try:
            with open(tab, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    cols = line.rstrip("\n").split("\t")
                    if len(cols) >= 3 and cols[2] == tz:
                        lat, lon = _parse_iso6709(cols[1])
                        return lat, lon, tz
        except (OSError, ValueError, IndexError):
            continue
    return None


def day_off_active(cfg, now=None):
    """True when the daytime-off option is on, a location is set and it is day."""
    s = settings(cfg)
    if not s["day_off_enabled"] or s["latitude"] is None:
        return False
    return is_daytime(s["latitude"], s["longitude"], now)
