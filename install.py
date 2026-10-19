#!/usr/bin/env python3
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"

APP_ID = "io.github.Piyush_1927.Hayate"
PYPI_PKG = "hayate-player"
SCRIPT_DIR = Path(__file__).resolve().parent

RELEASE_URL = "https://github.com/Piyush-1927/Hayate-Multi-Platform/releases/latest/download"
INSTALLER_NAME = "Hayate-Setup.exe"
PORTABLE_NAME = "Hayate-Windows-portable.zip"


def detect_system():
    system = platform.system().lower()
    distro = "unknown"

    if system == "linux":
        os_release = Path("/etc/os-release")
        if os_release.exists():
            data = {}
            for line in os_release.read_text().splitlines():
                if "=" in line:
                    key, val = line.split("=", 1)
                    data[key.strip()] = val.strip().strip('"').strip("'")

            distro_id = data.get("ID", "").lower()
            id_like = data.get("ID_LIKE", "").lower().split()

            if distro_id == "arch" or "arch" in id_like:
                distro = "arch"
            else:
                distro = distro_id or "linux"
        else:
            distro = "linux"

    return system, distro


def install_pipx():
    print(f"\n{BOLD}{BLUE}[*] Running pipx installation...{NC}")
    if not shutil.which("pipx"):
        print(f"{RED}[!] pipx is not installed on this system.{NC}")
        print("Please install pipx first:")
        print("  - Arch:    sudo pacman -S python-pipx")
        print("  - macOS:   brew install pipx")
        print("  - Windows: py -m pip install --user pipx")
        print("  - Ubuntu:  sudo apt install pipx")
        sys.exit(1)

    try:
        subprocess.run(["pipx", "install", "--force", PYPI_PKG], check=True)
        print(
            f"\n{GREEN}[✓] Hayate installed via pipx! Run it anytime using:{NC} {BOLD}hayate{NC}"
        )
    except subprocess.CalledProcessError as e:
        print(f"{RED}[!] pipx installation failed with code {e.returncode}{NC}")
        sys.exit(e.returncode)


def install_flatpak():
    print(f"\n{BOLD}{BLUE}[*] Running Flatpak installation...{NC}")
    if not shutil.which("flatpak") or not shutil.which("flatpak-builder"):
        print(f"{RED}[!] flatpak and flatpak-builder must be installed.{NC}")
        sys.exit(1)

    manifest = SCRIPT_DIR / f"{APP_ID}.yaml"
    build_dir = SCRIPT_DIR / "build-dir"

    if not manifest.exists():
        print(f"{RED}[!] Manifest file '{manifest.name}' not found.{NC}")
        sys.exit(1)

    cmd = [
        "flatpak-builder",
        "--user",
        "--install",
        "--force-clean",
        str(build_dir),
        str(manifest),
    ]

    try:
        subprocess.run(cmd, check=True)
        print(
            f"\n{GREEN}[✓] Hayate Flatpak installed successfully! Run it using:{NC} {BOLD}flatpak run {APP_ID}{NC}"
        )
    except subprocess.CalledProcessError as e:
        print(f"{RED}[!] Flatpak build failed with code {e.returncode}{NC}")
        sys.exit(e.returncode)


def install_makepkg():
    print(f"\n{BOLD}{BLUE}[*] Building native Arch package via makepkg...{NC}")
    if not shutil.which("makepkg"):
        print(f"{RED}[!] makepkg is not installed. Ensure base-devel is installed.{NC}")
        sys.exit(1)

    pkgbuild_path = SCRIPT_DIR / "PKGBUILD"
    if not pkgbuild_path.exists():
        print(f"{RED}[!] PKGBUILD not found in {SCRIPT_DIR}.{NC}")
        sys.exit(1)

    try:
        subprocess.run(["makepkg", "-sif", "--noconfirm"], cwd=SCRIPT_DIR, check=True)
        print(
            f"\n{GREEN}[✓] Hayate installed via pacman! Run it using:{NC} {BOLD}hayate{NC}"
        )
    except subprocess.CalledProcessError as e:
        print(f"{RED}[!] makepkg build failed with code {e.returncode}{NC}")
        sys.exit(e.returncode)




def download_release_asset(name, destination_dir):
    url = f"{RELEASE_URL}/{name}"
    destination = Path(destination_dir) / name
    print(f"{BLUE}[*] Downloading {url}{NC}")
    try:
        urllib.request.urlretrieve(url, destination)
    except (OSError, ValueError) as e:
        print(f"{RED}[!] Download failed: {e}{NC}")
        sys.exit(1)
    return destination


def extract_portable_zip(archive, target):
    """Reject archive paths that resolve outside the destination."""
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()

    try:
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                name = member.filename.replace("\\", "/")

                if name.startswith("/") or (
                    len(name) >= 2 and name[1] == ":"
                ):
                    raise ValueError(f"Unsafe ZIP path: {member.filename!r}")

                destination = (root / name).resolve()
                if not destination.is_relative_to(root):
                    raise ValueError(f"Unsafe ZIP path: {member.filename!r}")

            z.extractall(root)
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as e:
        print(f"{RED}[!] Portable extraction failed: {e}{NC}")
        sys.exit(1)


def install_windows_installer():
    print(f"\n{BOLD}{BLUE}[*] Downloading Windows installer...{NC}")
    with tempfile.TemporaryDirectory(prefix="hayate-installer-") as tmp:
        exe = download_release_asset(INSTALLER_NAME, tmp)
        try:
            subprocess.run([str(exe)], check=True)
        except subprocess.CalledProcessError as e:
            print(f"{RED}[!] Installer exited with code {e.returncode}{NC}")
            sys.exit(e.returncode)
        except OSError as e:
            print(f"{RED}[!] Could not launch installer: {e}{NC}")
            sys.exit(1)

    print(f"{GREEN}[✓] Installer finished.{NC}")


def install_windows_portable():
    print(f"\n{BOLD}{BLUE}[*] Downloading portable ZIP...{NC}")
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    target = base / "Hayate"

    with tempfile.TemporaryDirectory(prefix="hayate-portable-") as tmp:
        archive = download_release_asset(PORTABLE_NAME, tmp)
        extract_portable_zip(archive, target)

    executables = sorted(target.rglob("Hayate.exe"))
    print(f"{GREEN}[✓] Portable files extracted to: {target}{NC}")
    if executables:
        print(f"Run Hayate using: {executables[0]}")
    else:
        print("Hayate.exe was not found; check the ZIP's directory structure.")


def main():
    system, distro = detect_system()
    print(f"{BOLD}{BLUE}=== Hayate Installer ==={NC}\n")

    if system == "windows":
        print(f"Detected Operating System: {BOLD}{YELLOW}Windows{NC}")
        print("\\nPlease choose an installation method:")
        print("  1) Windows installer (Recommended - Hayate-Setup.exe)")
        print("  2) Portable ZIP (extracts to %LOCALAPPDATA%\\\\Hayate)")
        print("  3) pipx (Python package)")
        print("  4) Quit")

        choice = input("\\nEnter choice [1-4]: ").strip()
        if choice == "1":
            install_windows_installer()
        elif choice == "2":
            install_windows_portable()
        elif choice == "3":
            install_pipx()
        else:
            print("Exiting.")

    elif system == "darwin":
        print(f"Detected Operating System: {BOLD}{YELLOW}macOS{NC}")
        print(f"Recommended installation method: {BOLD}pipx{NC}")
        choice = input("Proceed with pipx installation? [Y/n]: ").strip().lower()
        if choice in ("", "y", "yes"):
            install_pipx()
        else:
            print("Installation cancelled.")

    elif system == "linux":
        if distro == "arch":
            print(f"Detected Linux Distribution: {BOLD}{YELLOW}Arch Linux{NC}")
            print("\nPlease choose an installation method:")
            print("  1) pipx (Recommended - isolated sandbox with global binary)")
            print("  2) Flatpak (Sandboxed desktop application)")
            print("  3) makepkg (Native pacman package via PKGBUILD)")
            print("  4) Quit")

            choice = input("\nEnter choice [1-4]: ").strip()
            if choice == "1":
                install_pipx()
            elif choice == "2":
                install_flatpak()
            elif choice == "3":
                install_makepkg()
            else:
                print("Exiting.")
        else:
            print(f"Detected Linux Distribution: {BOLD}{YELLOW}{distro}{NC}")
            print("\nPlease choose an installation method:")
            print("  1) pipx (Isolated CLI environment)")
            print("  2) Flatpak (Sandboxed desktop application)")
            print("  3) Quit")

            choice = input("\nEnter choice [1-3]: ").strip()
            if choice == "1":
                install_pipx()
            elif choice == "2":
                install_flatpak()
            else:
                print("Exiting.")
    else:
        print(f"{RED}[!] Unrecognized operating system: {system}{NC}")
        print("Defaulting to pipx installer.")
        install_pipx()


if __name__ == "__main__":
    main()
