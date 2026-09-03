"""
Minecraft Mod Download Counter
Fetches download counts from Modrinth (API) and CurseForge (Playwright).
Also fetches per-version/file data for sensors starting from version 0.3.
"""

import json
import os
import requests
import asyncio
from playwright.async_api import async_playwright
import re
import sys


def get_modrinth_downloads(slug: str) -> int:
    """
    Fetch download count from Modrinth using their public API.

    Args:
        slug: The mod's slug (e.g., 'create-photomovement')

    Returns:
        Total download count
    """
    if not slug:
        return 0

    url = f"https://api.modrinth.com/v2/project/{slug}"
    headers = {
        "User-Agent": "ModDownloadCounter/1.0 (github.com/your-username)"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        return data.get("downloads", 0)
    else:
        print(f"Modrinth API error: {response.status_code}")
        return 0


async def get_curseforge_downloads(project_path: str, debug: bool = False) -> int:
    """
    Fetch download count from CurseForge using Playwright.

    Args:
        project_path: The mod's path (e.g., 'minecraft/mc-mods/create-photomovement')
        debug: If True, saves HTML to file for debugging

    Returns:
        Total download count
    """
    url = f"https://www.curseforge.com/{project_path}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)

            # Wait for page content to fully load
            await page.wait_for_timeout(3000)

            content = await page.content()

            # Debug: save HTML for inspection
            if debug:
                with open("curseforge_debug.html", "w", encoding="utf-8") as f:
                    f.write(content)
                print("  [DEBUG] Saved page HTML to curseforge_debug.html")

            download_count = 0

            # Strategy 1: Look for the project stats section
            # CurseForge typically shows stats like "X Downloads"
            try:
                # Try finding stats in various common locations
                stats_selectors = [
                    ".project-details__stats",
                    ".stats-details",
                    "[class*='Stats']",
                    "aside",
                ]

                for selector in stats_selectors:
                    elements = page.locator(selector)
                    count = await elements.count()
                    if count > 0:
                        text = await elements.first.text_content()
                        if text:
                            # Look for download pattern in text
                            match = re.search(r'([\d,]+)\s*(?:Total\s*)?Downloads', text, re.IGNORECASE)
                            if match:
                                download_count = int(match.group(1).replace(',', ''))
                                return download_count
            except Exception as e:
                if debug:
                    print(f"  [DEBUG] Stats selector strategy failed: {e}")

            # Strategy 2: Search for the Downloads pattern in details section
            # CurseForge uses: <dt><span>Downloads</span></dt><dd>1,187</dd>
            patterns = [
                r'<dt>\s*<span>Downloads</span>\s*</dt>\s*<dd>([\d,]+)</dd>',
                r'>Downloads</span></dt>\s*<dd>([\d,]+)</dd>',
                r'Downloads\s*</dt>\s*<dd>\s*([\d,]+)\s*</dd>',
                r'(\d{1,3}(?:,\d{3})*)\s*(?:Total\s*)?Downloads',
                r'"downloadCount"\s*:\s*(\d+)',
                r'"downloads"\s*:\s*(\d+)',
            ]

            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    download_count = int(match.group(1).replace(',', ''))
                    if download_count > 0:
                        return download_count

            # Strategy 3: Look for JSON data embedded in the page
            json_match = re.search(r'<script[^>]*>.*?"downloads":\s*(\d+).*?</script>', content, re.DOTALL)
            if json_match:
                return int(json_match.group(1))

            print("  [WARN] Could not find download count on CurseForge page")
            if not debug:
                print("  [TIP] Run with --debug flag to save HTML for inspection")
            return 0

        except Exception as e:
            print(f"  [ERROR] CurseForge scraping error: {e}")
            return 0
        finally:
            await browser.close()


def _parse_version_tuple(version_str: str) -> tuple:
    """Parse a version string like '0.3.1' into a comparable integer tuple."""
    parts = []
    for part in re.split(r'[.\-]', version_str):
        try:
            parts.append(int(part))
        except ValueError:
            pass
    return tuple(parts) if parts else (0,)


def _is_version_gte(version_str: str, min_version: str) -> bool:
    """Return True if version_str is greater than or equal to min_version."""
    return _parse_version_tuple(version_str) >= _parse_version_tuple(min_version)


def _extract_clean_mod_version(version_str: str) -> str:
    """
    Extract the pure semantic mod version from a Modrinth version string.
    The mod version is always the FIRST x.y.z number in the string.
    e.g. '0.3.3+fabric-1.20.1' -> '0.3.3'
         'MC1201-0.3.1'        -> '0.3.1'
         '0.3.0'               -> '0.3.0'
    """
    match = re.search(r'\d+\.\d+\.\d+(?:\.\d+)?', version_str)
    return match.group(0) if match else version_str


def _extract_version_from_filename(filename: str) -> str:
    """
    Extract the mod version number from a CurseForge jar filename.
    Filenames follow the pattern: modname-loader-<mc-version>-<mod-version>.jar
    The mod version is always the LAST version-like number in the filename,
    e.g. 'createphotomovement-neoforge-1.21.1-0.3.3.jar' -> '0.3.3'.
    """
    matches = re.findall(r'\d+\.\d+\.\d+(?:\.\d+)?', filename)
    return matches[-1] if matches else ""


def _extract_loaders_from_filename(filename: str) -> list:
    """
    Extract mod loader names from a jar filename.
    Looks for known loader names (forge, neoforge, fabric, quilt).
    """
    loaders = []
    lower = filename.lower()
    for loader in ["neoforge", "forge", "fabric", "quilt"]:
        if loader in lower:
            loaders.append(loader)
    return loaders


def get_modrinth_versions(slug: str, min_version: str = "0.3") -> list:
    """
    Fetch per-version download data from the Modrinth API.
    Returns a list of dicts for all versions >= min_version.

    Each dict contains: version, name, game_versions, loaders, downloads,
    uploaded, version_type.
    """
    if not slug:
        return []

    url = f"https://api.modrinth.com/v2/project/{slug}/version"
    headers = {
        "User-Agent": "ModDownloadCounter/1.0 (github.com/your-username)"
    }

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print(f"Modrinth versions API error: {response.status_code}")
        return []

    versions = response.json()
    results = []

    for v in versions:
        version_number = v.get("version_number", "")

        # Only include versions >= min_version
        if not _is_version_gte(version_number, min_version):
            continue

        results.append({
            "version": version_number,
            "clean_version": _extract_clean_mod_version(version_number),
            "name": v.get("name", ""),
            "game_versions": v.get("game_versions", []),
            "loaders": v.get("loaders", []),
            "downloads": v.get("downloads", 0),
            "uploaded": v.get("date_published", ""),
            "version_type": v.get("version_type", "release"),
        })

    return results


def _parse_curseforge_files_from_html(html: str) -> list:
    """
    Extract the files array from CurseForge's Next.js streaming HTML.

    CurseForge embeds file data in script tags of the form:
      self.__next_f.push([1, "...escaped JSON string..."])
    Multiple push calls may contain partial file data, so we collect the
    chunk with the most file entries (dict type) and return that.
    We use json.JSONDecoder.raw_decode so JSON string escapes are handled
    correctly without a fragile regex.
    """
    decoder = json.JSONDecoder()
    search_str = "self.__next_f.push("
    pos = 0
    best_files = []  # track the chunk with the most file dicts

    while True:
        idx = html.find(search_str, pos)
        if idx < 0:
            break

        arg_start = idx + len(search_str)
        try:
            # raw_decode parses the JSON value ([1, "..."]) starting at arg_start
            arr, _ = decoder.raw_decode(html, arg_start)
        except json.JSONDecodeError:
            pos = arg_start
            continue

        pos = arg_start + 1  # advance past this call for the next iteration

        # We only care about streaming chunks: [1, "<payload string>"]
        if not (isinstance(arr, list) and len(arr) == 2 and isinstance(arr[1], str)):
            continue

        payload = arr[1]
        if '"files"' not in payload:
            continue

        # Locate the start of the files array and parse it
        fi = payload.find('"files":')
        if fi < 0:
            continue
        try:
            array_start = payload.index('[', fi)
            files_data, _ = decoder.raw_decode(payload, array_start)
        except (ValueError, json.JSONDecodeError):
            continue

        if not isinstance(files_data, list):
            continue

        # Only collect entries that are full file objects (dicts)
        file_dicts = []
        for entry in files_data:
            if not isinstance(entry, dict) or "fileName" not in entry:
                continue

            file_name = entry.get("fileName", entry.get("displayName", ""))

            # Loaders are in 'flavors' (list) or 'flavor' (single object)
            flavors = entry.get("flavors") or []
            if not flavors and entry.get("flavor"):
                flavors = [entry["flavor"]]
            loaders = [f["name"].lower() for f in flavors if isinstance(f, dict) and "name" in f]
            if not loaders:
                loaders = _extract_loaders_from_filename(file_name)

            file_dicts.append({
                "file_name": file_name,
                "game_versions": entry.get("gameVersions", [entry.get("primaryGameVersion", "")]),
                "loaders": loaders,
                "downloads": entry.get("totalDownloads", 0),
                "uploaded": entry.get("dateCreated", ""),
                "release_type": entry.get("releaseType", 1),
            })

        # Keep the chunk with the most complete file list
        if len(file_dicts) > len(best_files):
            best_files = file_dicts

    return best_files


async def get_curseforge_versions(project_path: str, min_version: str = "0.3", debug: bool = False) -> list:
    """
    Fetch per-file download data from the CurseForge files page.
    Parses the files list from Next.js streaming script tags embedded in the HTML.
    Returns a list of dicts for files with versions >= min_version.

    Each dict contains: version, file_name, game_versions, loaders,
    downloads, uploaded, release_type.
    """
    if not project_path:
        return []

    url = f"https://www.curseforge.com/{project_path}/files/all?page=1&pageSize=20&showAlphaFiles=hide"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(3000)

            content = await page.content()

            if debug:
                with open("curseforge_files_debug.html", "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"  [DEBUG] Saved files page HTML ({len(content)} bytes)")

            all_files = _parse_curseforge_files_from_html(content)

            if debug:
                print(f"  [DEBUG] Parsed {len(all_files)} total file entries from HTML")

            # Filter by version and attach the version string
            results = []
            for entry in all_files:
                version = _extract_version_from_filename(entry["file_name"])
                if not version or not _is_version_gte(version, min_version):
                    continue
                results.append({**entry, "version": version})

            return results

        except Exception as e:
            print(f"  [ERROR] CurseForge versions scraping error: {e}")
            return []
        finally:
            await browser.close()


def _make_sensor_id(mod_name: str, platform: str, mod_version: str, mc_version: str, loaders: list) -> str:
    """
    Build a Home Assistant entity ID for a per-version sensor.
    Format: sensor.mod_<name>_<platform>_<mod_version>_<mc_version>_<loader(s)>
    Example: sensor.mod_create_photomovement_modrinth_033_1211_neoforge
    """
    def sanitize(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '', s.lower())

    safe_name = re.sub(r'[^a-z0-9_]', '_', mod_name.lower())
    v = sanitize(mod_version)
    gv = sanitize(mc_version)
    loader_str = "_".join(sanitize(l) for l in loaders) if loaders else "unknown"
    return f"sensor.mod_{safe_name}_{platform}_{v}_{gv}_{loader_str}"


def send_version_sensors_to_home_assistant(
    modrinth_versions: list,
    curseforge_versions: list,
    ha_url: str,
    token: str,
    mod_name: str = "",
):
    """
    Send per-version/file download sensors to Home Assistant.
    Creates one sensor per platform+mod_version+mc_version+loader combination.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    sensors = []

    # Build one sensor per Modrinth version entry
    for v in modrinth_versions:
        primary_gv = v["game_versions"][0] if v["game_versions"] else "unknown"
        entity_id = _make_sensor_id(mod_name, "modrinth", v["clean_version"], primary_gv, v["loaders"])
        loader_str = ",".join(v["loaders"])
        sensors.append({
            "entity_id": entity_id,
            "state": v["downloads"],
            "attributes": {
                "friendly_name": f"{mod_name} - Modrinth {v['clean_version']} {primary_gv} {loader_str} Downloads",
                "icon": "mdi:download",
                "unit_of_measurement": "downloads",
                "mod_version": v["clean_version"],
                "game_version": primary_gv,
                "platform": loader_str,
                "uploaded": v["uploaded"][:10] if v["uploaded"] else "",
                "source": "modrinth",
            },
        })

    # Build one sensor per CurseForge file entry
    for v in curseforge_versions:
        primary_gv = v["game_versions"][0] if v["game_versions"] else "unknown"
        entity_id = _make_sensor_id(mod_name, "curseforge", v["version"], primary_gv, v["loaders"])
        loader_str = ",".join(v["loaders"])
        sensors.append({
            "entity_id": entity_id,
            "state": v["downloads"],
            "attributes": {
                "friendly_name": f"{mod_name} - CurseForge {v['version']} {primary_gv} {loader_str} Downloads",
                "icon": "mdi:download",
                "unit_of_measurement": "downloads",
                "mod_version": v["version"],
                "game_version": primary_gv,
                "platform": loader_str,
                "uploaded": v["uploaded"][:10] if v["uploaded"] else "",
                "source": "curseforge",
            },
        })

    for sensor in sensors:
        entity_id = sensor["entity_id"]
        url = f"{ha_url}/api/states/{entity_id}"
        payload = {"state": sensor["state"], "attributes": sensor["attributes"]}
        try:
            response = requests.post(url, headers=headers, json=payload)
            if response.status_code in [200, 201]:
                print(f"  Updated {entity_id}: {sensor['state']} downloads")
            else:
                print(f"  Failed {entity_id}: {response.status_code} - {response.text}")
        except requests.exceptions.ConnectionError:
            print(f"  Could not connect to Home Assistant at {ha_url}")
            break
        except Exception as e:
            print(f"  Error updating {entity_id}: {e}")


async def main():
    # Path where Home Assistant add-ons store their configuration
    options_path = "/data/options.json"

    if os.path.exists(options_path):
        # Running as a Home Assistant add-on
        with open(options_path, "r") as f:
            options = json.load(f)
        # Read the mods list defined in the add-on options/schema
        mods = options.get("mods", [])
        # SUPERVISOR_TOKEN is injected automatically by the HA supervisor
        HA_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
        HA_URL = "http://supervisor/core"
    else:
        # Standalone / development mode
        mods = [{
            "name": "create_photomovement",
            "modrinth_slug": "create-photomovement",
            "curseforge_path": "minecraft/mc-mods/create-photomovement",
        }]
        # Provide the token via the HA_TOKEN environment variable when testing locally
        HA_TOKEN = os.environ.get("HA_TOKEN", "")
        HA_URL = "http://localhost:8123"

    # Check for debug flag
    debug_mode = "--debug" in sys.argv

    print("=" * 50)
    print("Minecraft Mod Download Counter")
    print(f"Tracking {len(mods)} mod(s)")
    print("=" * 50)
    print(f"  HA URL:   {HA_URL}")
    print(f"  HA token: {'set' if HA_TOKEN else 'NOT SET'}")
    print("=" * 50)

    all_results = []

    for mod in mods:
        mod_name = mod.get("name", "unknown")
        modrinth_slug = mod.get("modrinth_slug", "")
        curseforge_path = mod.get("curseforge_path", "")

        print(f"\n--- {mod_name} ---")
        print(f"  Modrinth slug:   {modrinth_slug or '(not set)'}")
        print(f"  CurseForge path: {curseforge_path or '(not set)'}")

        # Fetch total download counts
        print("  Fetching Modrinth downloads...")
        modrinth_downloads = get_modrinth_downloads(modrinth_slug)
        print(f"    Modrinth: {modrinth_downloads:,} downloads")

        print("  Fetching CurseForge downloads...")
        curseforge_downloads = await get_curseforge_downloads(curseforge_path, debug=debug_mode)
        print(f"    CurseForge: {curseforge_downloads:,} downloads")

        total = modrinth_downloads + curseforge_downloads
        print(f"  Total: {total:,} downloads")

        results = {
            "name": mod_name,
            "modrinth": modrinth_downloads,
            "curseforge": curseforge_downloads,
            "total": total,
        }
        all_results.append(results)

        # Fetch per-version data
        print("  Fetching Modrinth per-version data (>= 0.3)...")
        modrinth_versions = get_modrinth_versions(modrinth_slug)
        print(f"    Found {len(modrinth_versions)} version(s)")

        print("  Fetching CurseForge per-file data (>= 0.3)...")
        curseforge_versions = await get_curseforge_versions(curseforge_path, debug=debug_mode)
        print(f"    Found {len(curseforge_versions)} file(s)")

        # Send sensors to Home Assistant
        if HA_TOKEN:
            print("  Sending totals to Home Assistant...")
            send_to_home_assistant(results, HA_URL, HA_TOKEN)

            print("  Sending per-version sensors to Home Assistant...")
            send_version_sensors_to_home_assistant(
                modrinth_versions, curseforge_versions, HA_URL, HA_TOKEN, mod_name
            )
        else:
            print("  [WARN] No HA token available, skipping Home Assistant update")

    print()
    print("=" * 50)
    grand_total = sum(r["total"] for r in all_results)
    print(f"GRAND TOTAL: {grand_total:,} downloads across {len(mods)} mod(s)")
    print("=" * 50)

    return all_results


def send_to_home_assistant(data: dict, ha_url: str, token: str):
    """
    Send download counts to Home Assistant as sensor entities.

    Args:
        data: Dictionary with name, modrinth, curseforge, and total counts
        ha_url: Home Assistant URL (e.g., 'http://localhost:8123')
        token: Long-lived access token
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Derive a safe entity-ID prefix from the mod name
    name = data.get("name", "mod")
    safe_name = re.sub(r'[^a-z0-9_]', '_', name.lower())

    # Define sensors to create/update
    sensors = [
        {
            "entity_id": f"sensor.mod_{safe_name}_modrinth",
            "state": data["modrinth"],
            "attributes": {
                "friendly_name": f"{name} - Modrinth Downloads",
                "icon": "mdi:download",
                "unit_of_measurement": "downloads",
                "source": "modrinth"
            }
        },
        {
            "entity_id": f"sensor.mod_{safe_name}_curseforge",
            "state": data["curseforge"],
            "attributes": {
                "friendly_name": f"{name} - CurseForge Downloads",
                "icon": "mdi:download",
                "unit_of_measurement": "downloads",
                "source": "curseforge"
            }
        },
        {
            "entity_id": f"sensor.mod_{safe_name}_total",
            "state": data["total"],
            "attributes": {
                "friendly_name": f"{name} - Total Downloads",
                "icon": "mdi:download-multiple",
                "unit_of_measurement": "downloads",
                "modrinth": data["modrinth"],
                "curseforge": data["curseforge"]
            }
        }
    ]

    for sensor in sensors:
        entity_id = sensor["entity_id"]
        url = f"{ha_url}/api/states/{entity_id}"

        payload = {
            "state": sensor["state"],
            "attributes": sensor["attributes"]
        }

        try:
            response = requests.post(url, headers=headers, json=payload)
            if response.status_code in [200, 201]:
                print(f"  ✓ Updated {entity_id}")
            else:
                print(f"  ✗ Failed to update {entity_id}: {response.status_code} - {response.text}")
        except requests.exceptions.ConnectionError:
            print(f"  ✗ Could not connect to Home Assistant at {ha_url}")
            break
        except Exception as e:
            print(f"  ✗ Error updating {entity_id}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
