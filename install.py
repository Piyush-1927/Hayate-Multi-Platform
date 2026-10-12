#!/usr/bin/env python3
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"

APP_ID = "io.github.Piyush_1927.Hayate"
PYPI_PKG = "hayate-player"
SCRIPT_DIR = Path(__file__).resolve().parent


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


def main():
    system, distro = detect_system()
    print(f"{BOLD}{BLUE}=== Hayate Installer ==={NC}\n")

    if system in ("darwin", "windows"):
        os_label = "macOS" if system == "darwin" else "Windows"
        print(f"Detected Operating System: {BOLD}{YELLOW}{os_label}{NC}")
        print(f"Recommended installation method: {BOLD}pipx{NC}")
        choice = (
            input("Proceed with pipx installation? [Y/n]: ").strip().lower()
        )
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
