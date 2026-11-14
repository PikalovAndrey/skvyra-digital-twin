"""Verify the development environment (`make env`). Prints what is present, never installs anything."""
import importlib
import os
import platform
import shutil
import subprocess
import sys

OK, MISSING = "ok ", "-- "
rows = []


def check(name, fn):
    try:
        rows.append((OK, name, fn()))
    except Exception as e:  # report and continue: the audit itself must not fail
        rows.append((MISSING, name, str(e).splitlines()[0][:120]))


def cmd(*a):
    return subprocess.run(a, capture_output=True, text=True, timeout=60).stdout.strip().splitlines()[0]


check("os", lambda: f"{platform.system()} {platform.release()} {platform.machine()}")
check("python", lambda: sys.version.split()[0] + ("" if sys.version_info >= (3, 12) else "  (need >= 3.12)"))
for mod in ("shapely", "pyproj", "numpy", "rasterio", "matplotlib", "PIL", "pytest"):
    check(mod, lambda m=mod: importlib.import_module(m).__version__)
check("GEOS (bundled)", lambda: ".".join(map(str, importlib.import_module("shapely").geos_version)))
check("PROJ (bundled)", lambda: importlib.import_module("pyproj").proj_version_str)
check("GDAL (bundled)", lambda: importlib.import_module("rasterio").__gdal_version__)
blender = shutil.which("blender") or "/Applications/Blender.app/Contents/MacOS/Blender"
check("blender", lambda: cmd(blender, "--version"))
check("node", lambda: cmd("node", "--version"))
check("npm", lambda: cmd("npm", "--version"))
check("ffmpeg (optional, flyover video)", lambda: cmd("ffmpeg", "-version")[:40])
check("disk free", lambda: f"{shutil.disk_usage(os.getcwd()).free / 2**30:.0f} GB")
if platform.system() == "Darwin":
    check("cpu", lambda: cmd("sysctl", "-n", "machdep.cpu.brand_string"))
    check("ram", lambda: f"{int(cmd('sysctl', '-n', 'hw.memsize')) / 2**30:.0f} GB")
for status, name, value in rows:
    print(f"{status}{name:<34}{value}")
sys.exit(1 if any(s == MISSING and n in ("python", "shapely", "pyproj", "numpy", "rasterio") for s, n, _ in rows) else 0)
