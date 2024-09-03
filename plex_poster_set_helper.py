import json
import math
import os
import re
import stat
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from plexapi.server import PlexServer
import plexapi.exceptions


LABEL_RATING_KEYS = {}
MEDIA_TYPES_PARENT_VALUES = {
    "movie": 1,
    "show": 2,
    "season": 2,
    "episode": 2,
    "album": 9,
    "track": 9,
}

# Plex Configuration settings
base_url = ""
token = ""

# Directories
assets_directory = "assets"
asset_folders = True

# App settings
append_label = "Overlay"
overwrite_existing_assets = False
overwrite_labelled_shows = False
only_process_new_assets = True
useragent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# Data containers
tv = []
movies = []
collections = []


def plex_setup():
    global tv, movies, collections, append_label, overwrite_labelled_shows, assets_directory, overwrite_existing_assets, base_url, token, asset_folders, only_process_new_assets, useragent

    def load_config(filename="config.json"):
        with open(filename) as f:
            return json.load(f)

    def handle_plex_exception(exception, message):
        sys.exit(f"{message}: {exception}. Please consult the readme.md.")

    def get_plex_library(plex, library_list, name):
        if isinstance(library_list, str):
            library_list = [library_list]
        elif not isinstance(library_list, list):
            handle_plex_exception(e, f"{name} must be either a string or a list")
        
        result = []
        for lib in library_list:
            try:
                result.append(plex.library.section(lib))
            except plexapi.exceptions.NotFound:
                handle_plex_exception(e, f"{name} library named not found in config.json")
        return result

    if os.path.exists("config.json"):
        try:
            config = load_config()
            base_url = config.get("base_url", "").rstrip("/")
            token = config.get("token", "")
            tv_library = config.get("tv_library")
            movie_library = config.get("movie_library")
            append_label = config.get("append_label", "Overlay")
            assets_directory = config.get("assets_directory", "assets")
            overwrite_existing_assets = config.get("overwrite_existing_assets", False)
            overwrite_labelled_shows = config.get("overwrite_labelled_shows", False)
            asset_folders = config.get("asset_folders", True)
            only_process_new_assets = config.get("only_process_new_assets", True)
            useragent = config.get("useragent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

            plex = PlexServer(base_url, token)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            handle_plex_exception(e, "Error with config.json file")
        except requests.exceptions.RequestException as e:
            handle_plex_exception(e, 'Unable to connect to Plex server')
        except plexapi.exceptions.Unauthorized as e:
            handle_plex_exception(e, 'Invalid Plex token')

        tv = get_plex_library(plex, tv_library, "TV")
        movies = get_plex_library(plex, movie_library, "Movie")
    else:
        handle_plex_exception(e, f"No config.json file found")


def cook_soup(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36", "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": "Windows", }

    response = requests.get(url, headers=headers)
    
    if response.status_code == 200 or (response.status_code == 500 and "mediux.pro" in url):
        return BeautifulSoup(response.text, "html.parser")
    
    sys.exit(f"Failed to retrieve the page. Status code: {response.status_code}")


def get_asset_file_path(assets_dir, folder_name, file_name):
    return os.path.join(assets_dir, folder_name, file_name)


def ensure_directory(directory_path):
    if not os.path.exists(directory_path):
        try:
            os.makedirs(directory_path, mode=0o755)
            print(f"Directory created: {directory_path}")
        except OSError as e:
            print(f"Failed to create directory: {e}")


def save_to_assets_directory(assets_dir, plex_folder, file_name, file_url):
    file_path = get_asset_file_path(assets_dir, plex_folder, file_name)

    # Check if file exists and handle overwriting
    if os.path.exists(file_path):
        if not overwrite_existing_assets:
            print(f"File already exists and overwriting is disabled: {file_path}")
            return file_path
        print(f"Overwriting existing file: {file_path}")

    # Ensure the directory exists
    plex_folder_path = os.path.dirname(file_path)
    os.makedirs(plex_folder_path, exist_ok=True)

     # Download and save the file
    headers = {"User-Agent": useragent}
    try:
        response = requests.get(file_url, headers=headers, stream=True)
        response.raise_for_status()
        
        with open(file_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        
        print(f"File downloaded and saved to: {file_path}")
        return file_path
    except requests.RequestException as e:
        print(f"Failed to download file. Error: {e}")
        return None
    except IOError as e:
        print(f"Failed to save file to assets directory. Error: {e}")
        return None


def title_cleaner(string):
    for delimiter in [" (", " -"]:
        if delimiter in string:
            return string.split(delimiter)[0].strip()
    return string.strip()



def parse_string_to_dict(input_string):
    # Clean up the string by replacing escape sequences and special characters
    cleaned_string = (input_string
                      .replace('\\\\\\"', "")
                      .replace("\\", "")
                      .replace("u0026", "&"))

    # Extract and parse JSON data
    json_start = cleaned_string.find("{")
    json_end = cleaned_string.rfind("}") + 1
    json_data = cleaned_string[json_start:json_end]

    return json.loads(json_data)


def add_label_rating_key(library_item):
    existing_section = LABEL_RATING_KEYS.get(library_item.librarySectionID, {})

    if append_label and append_label not in library_item.labels:
        existing_keys = existing_section.get("keys", [])

        if str(library_item.ratingKey) not in existing_keys:
            existing_keys += [str(library_item.ratingKey)]

        existing_type = existing_section.get(
            "type", MEDIA_TYPES_PARENT_VALUES[library_item.type]
        )

        LABEL_RATING_KEYS[library_item.librarySectionID] = {
            "keys": existing_keys,
            "type": existing_type,
        }


def get_file_path_from_plex(rating_key):
    headers = {"X-Plex-Token": token}
    response = requests.get(f"{base_url}/library/metadata/{rating_key}", headers=headers)

    if response.status_code != 200:
        raise Exception(f"Failed to get metadata: {response.status_code}")

    # Parse the XML response
    try:
        root = ET.fromstring(response.text)
        location_element = root.find(".//Location")

        if location_element is not None:
            file_path = location_element.get("path")

            if not file_path:
                raise Exception("Path attribute not found in Location element")

            return os.path.basename(file_path)
        else:
            location_element = root.find(".//Part")

            if location_element is None:
                raise Exception("Location element not found in XML")

            file_path = location_element.get("file")

            if not file_path:
                raise Exception("File attribute not found in Part element")

            return os.path.basename(os.path.dirname(file_path))

    except ET.ParseError as e:
        raise Exception(f"Failed to parse XML: {e}")


def find_in_library(libraries, poster):
    for lib in libraries:
        try:
            kwargs = {'year': poster.get("year")} if poster.get("year") else {}
            library_item = lib.get(poster["title"], **kwargs)
            
            if library_item:
                add_label_rating_key(library_item)
                show_path = get_file_path_from_plex(library_item.ratingKey)
                return library_item, show_path

        except Exception as e:
            error_message = str(e)
            if "Unable to find item with title" not in error_message:
                print(e)
    
    return None, None


def find_collection(libraries, poster):
    collections = []
    
    for lib in libraries:
        try:
            for collection in lib.collections():
                if collection.title == poster["title"]:
                    collections.append(collection)
        except Exception as e:
            # Optionally log the exception if needed
            # print(f"Error retrieving collections: {e}")
            pass

    return collections if collections else None


def update_plex_labels():
    headers = {"X-Plex-Token": token}

    if not LABEL_RATING_KEYS:
        return

    for section_id, item in LABEL_RATING_KEYS.items():
        for rating_key in item["keys"]:
            if not check_label_for_item(rating_key):
                url = f"{base_url}/library/metadata/{rating_key}"
                params = {
                    "label.locked": 1,
                    "label[0].tag.tag": append_label,
                }

                try:
                    response = requests.put(url, headers=headers, params=params, timeout=10)

                    if response.status_code == 200:
                        print(f"Label '{append_label}' applied successfully to show with rating key {rating_key}")
                    else:
                        print(f"Failed to apply label '{append_label}' to show with rating key {rating_key} - {response.status_code}: {response.reason}")
                except requests.Timeout:
                    print(f"Request to show with rating key {rating_key} timed out.")
                except requests.RequestException as e:
                    print(f"Error updating labels for show with rating key {rating_key}: {e}")


def check_label_for_item(rating_key):
    headers = {"X-Plex-Token": token}
    url = f"{base_url}/library/metadata/{rating_key}"

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        root = ET.fromstring(response.content)
        labels = {label.get("tag") for label in root.findall(".//Label")}
        
        return append_label in labels

    except requests.RequestException as e:
        print(f"Error checking label for item with rating key {rating_key}: {e}")
    except ET.ParseError as e:
        print(f"Failed to parse XML response for rating key {rating_key}: {e}")
    
    return False


def upload_tv_poster(poster, tv):
    tv_show, show_path = find_in_library(tv, poster)
    
    if tv_show is None or show_path is None:
        print(f"{poster['title']} not found in tv libraries or failed to load path.")
        return
    
    if check_label_for_item(tv_show.ratingKey) and not overwrite_labelled_shows:
        print(f"Skipping upload for {show_path} as it already has the label '{append_label}'.")
        return
    
    season_str = str(poster.get("season", "")).zfill(2)
    episode_str = str(poster.get("episode", "")).zfill(2)
    
    if poster["season"] == "Cover":
        file_name = "poster.jpg"
    elif poster["season"] == "Backdrop":
        file_name = "background.jpg"
    elif poster["season"] >= 0:
        if poster["episode"] in {"Cover", None}:
            file_name = f"Season{season_str}.jpg"
        else:
            file_name = f"S{season_str}E{episode_str}.jpg"
    else:
        print(f"Skipping upload for {poster['url']} due to sorting error.")
        return
    
    file_name = file_name if asset_folders else f"{show_path}_{file_name}"
    asset_path = f"tv/{show_path}" if asset_folders else "tv"
    
    file_path = get_asset_file_path(assets_directory, asset_path, file_name)
    # Handle existing file scenarios
    if os.path.exists(file_path) and not overwrite_existing_assets:
        if only_process_new_assets:
            print(f"Skipping upload for {poster['title']} as only processing new assets in {tv_show.librarySectionTitle} library.")
            return
        print(f"Using existing file for upload to {poster['title']} in {tv_show.librarySectionTitle} library.")
    else:
        file_path = save_to_assets_directory(assets_directory, asset_path, file_name, poster["url"])
        if file_path is None:
            print(f"Skipping upload for {show_path} {poster['title']} due to download error.")
            return
    
    # Upload logic
    try:
        if poster["season"] == "Cover":
            upload_target = tv_show
            print(f"Uploading cover art for {poster['title']} - {poster['season']} in {tv_show.librarySectionTitle} library.")
        elif poster["season"] == "Backdrop":
            upload_target = tv_show
            print(f"Uploading cover art for {poster['title']} - {poster['season']} in {tv_show.librarySectionTitle} library.")
        elif poster["season"] == 0:
                upload_target = tv_show.season("Specials")
                print(f"Uploading art for {poster['title']} - Specials in {tv_show.librarySectionTitle} library.")
        elif poster["season"] >= 1:
            if poster["episode"] in {"Cover", None}:
                upload_target = tv_show
                print(f"Uploading cover art for {poster['title']} - {poster['season']} in {tv_show.librarySectionTitle} library.")
            elif poster["episode"] is not None:
                upload_target = tv_show.season(poster["season"]).episode(poster["episode"])
                print(f"Uploading art for {poster['title']} - Season {poster['season']} Episode {poster['episode']} in {tv_show.librarySectionTitle} library.")
            else:
                print(f"Skipping upload for {poster['url']} due to sorting error.")
                return
        if poster["season"] == "Backdrop":
            try:
                upload_target.uploadArt(filepath=file_path)
            except:
                print("Unable to upload background.")
        else:
            try:
                upload_target.uploadPoster(filepath=file_path)
            except:
                print(f"Unable to upload last poster. {file_path}")
            time.sleep(6)  # Timout to prevent spamming servers
                
    except Exception as e:
        print(f"Error uploading {poster['title']} - {e}")


def upload_movie_poster(poster, movies):
    movies, show_path = find_in_library(movies, poster)
    if not movies or not show_path:
        print(f"{poster['title']} not found in collections libraries or failed to load path.")
        return
        
    for movie in movies:
        if check_label_for_item(movie.ratingKey) and not overwrite_labelled_shows:
            print(f"Skipping upload for {poster['title']} in {movie_item.librarySectionTitle} library as it already has the label '{append_label}'.")
            return
            
        file_name = "poster.jpg" if asset_folders else f"{show_path}.jpg"
        asset_path = f"movies/{show_path}" if asset_folders else f"movies"
    
        file_path = get_asset_file_path(assets_directory, asset_path, file_name)
        if os.path.exists(file_path) and not overwrite_existing_assets:
            print(f"Skipping upload for {poster['title']} as it already exists in {movie.librarySectionTitle} library.")
            return
                
        file_path = save_to_assets_directory(assets_directory, asset_path, file_name, poster["url"])
        if not file_path:
            print(f"Skipping upload for {poster['title']} in {collection.librarySectionTitle} library due to download error.")
            return
                
        try:
            movie.uploadPoster(filepath=file_path)
            print(f'Uploaded art for {poster["title"]}.')
            time.sleep(6)  # Timout to prevent spamming servers
        except:
            print(f'Unable to upload art for {poster["title"]} in {movie.librarySectionTitle} library.')


def upload_collection_poster(poster, movies):
    collection_items = find_collection(movies, poster)
    if not collection_items:
        print(f"No collections found for {poster['title']}.")
        return

    for collection in collection_items:
        if collection is None:
            print(f"Skipping upload for {poster['title']}. Failed to process collection data.")
            return
        
        if poster["title"] is None:
            print(f"Skipping upload for {poster['title']}. Failed to process poster data.")
            return
        
        if check_label_for_item(collection.ratingKey) and not overwrite_labelled_shows:
            print(f"Skipping upload for {poster['title']} in {collection.librarySectionTitle} library as it already has the label '{append_label}'.")
            return

        file_name = "poster.jpg" if asset_folders else f"{poster['title']}.jpg"
        asset_path = f"collections/{poster['title']}" if asset_folders else "collections"
    
        file_path = get_asset_file_path(assets_directory, asset_path, file_name)
        if os.path.exists(file_path) and not overwrite_existing_assets:
            print(f"Skipping upload for {poster['title']} as it already exists in {collection.librarySectionTitle} library.")
            return
        
        file_path = save_to_assets_directory(assets_directory, asset_path, file_name, poster["url"])
        if not file_path:
            print(f"Skipping upload for {poster['title']} in {collection.librarySectionTitle} library due to download error.")
            return
            
        try:
            collection.uploadPoster(filepath=file_path)
            print(f'Uploaded art for {poster["title"]}.')
            time.sleep(6)  # Timout to prevent spamming servers
        except Exception as e:
            print(f'Unable to upload art for {poster["title"]} in {collection.librarySectionTitle} library. Error: {e}')


def set_posters(url):
    print(f"Setting posters for URL: {url}")

    result = scrape(url)

    if not result or len(result) != 3:
        #print("Scrape function did not return the expected 3 values.")
        return

    movieposters, showposters, collectionposters = result

    if not any([movieposters, showposters, collectionposters]):
        print("No posters found.")
        return

    for poster in collectionposters:
        upload_collection_poster(poster, collections)

    for poster in movieposters:
        upload_movie_poster(poster, movies)

    for poster in showposters:
        upload_tv_poster(poster, tv)

    update_plex_labels()



def scrape_posterdb_set_link(soup):
    try:
        return soup.find("a", class_="rounded view_all")["href"]
    except (TypeError, KeyError):
        return None


def scrape_posterd_user_info(soup):
    try:
        span_tag = soup.find("span", class_="numCount")
        number_str = span_tag["data-count"]
        upload_count = int(number_str)
        return math.ceil(upload_count / 24)
    except (AttributeError, KeyError, ValueError) as e:
        #print(f"Error extracting user info: {e}")
        return None


def scrape_mediux_user_info(base_url):
    current_page = 1
    total_pages = 1

    while True:
        page_url = f"{base_url}?page={current_page}"
        print(f"Processing page: {current_page}")
        soup = cook_soup(page_url)

        # Extract all page numbers from links
        page_links = soup.select('a[href*="page="]')
        page_numbers = [
            int(re.search(r"page=(\d+)", a["href"]).group(1))
            for a in page_links
            if re.search(r"page=(\d+)", a["href"])
        ]
        if page_numbers:
            total_pages = max(page_numbers)

        # Get the next page link
        next_page_link = soup.select_one('a[aria-label="Go to next page"]')
        if next_page_link and (match := re.search(r"page=(\d+)", next_page_link["href"])):
            current_page = int(match.group(1))
        else:
            break

    return total_pages


def scrape_posterdb(soup):
    movieposters = []
    showposters = []
    collectionposters = []

    # Find the poster grid
    poster_div = soup.find("div", class_="row d-flex flex-wrap m-0 w-100 mx-n1 mt-n1")
    if not poster_div:
        print("Poster grid not found.")
        return movieposters, showposters, collectionposters

    # Find all poster divs
    posters = poster_div.find_all("div", class_="col-6 col-lg-2 p-1")

    for poster in posters:
        # Determine if poster is for a show, movie, or collection
        media_type = poster.find("a", class_="text-white", attrs={"data-toggle": "tooltip", "data-placement": "top"}).get("title")
        # Get high resolution poster image
        overlay_div = poster.find("div", class_="overlay")
        poster_id = overlay_div.get("data-poster-id")
        poster_url = f"https://theposterdb.com/api/assets/{poster_id}"
        # Get metadata
        title_p = poster.find("p", class_="p-0 mb-1 text-break").get_text(strip=True)

        if media_type == "Show":
            title = title_cleaner(title_p)
            try:
                year = int(title_p.split(" (")[1].split(")")[0])
            except (IndexError, ValueError):
                year = None

            if " - " in title_p:
                split_season = title_p.split(" - ")[-1]
                if split_season == "Specials":
                    season = 0
                elif "Season" in split_season:
                    try:
                        season = int(split_season.split(" ")[1])
                    except (IndexError, ValueError):
                        season = None
            else:
                season = "Cover"

            showposters.append({"title": title, "url": poster_url, "season": season, "episode": None, "year": year, "source": "posterdb"})

        elif media_type == "Movie":
            title_split = title_p.split(" (")
            if len(title_split) == 2:
                title = title_split[0]
                year = title_split[1].split(")")[0]
                try:
                    year = int(year)
                except ValueError:
                    year = None
            else:
                title = title_p
                year = None

            movieposters.append({"title": title, "url": poster_url, "year": year, "source": "posterdb"})

        elif media_type == "Collection":
            collectionposters.append({"title": title_p, "url": poster_url, "source": "posterdb"})

    return movieposters, showposters, collectionposters


def get_mediux_filters():
    config = json.load(open("config.json"))
    return config.get("mediux_filters", None)


def check_mediux_filter(mediux_filters, filter):
    return filter in mediux_filters if mediux_filters else True


def scrape_mediux(soup):
    base_url = ("https://mediux.pro/_next/image?url=https%3A%2F%2Fapi.mediux.pro%2Fassets%2F")
    quality_suffix = "&w=3840&q=80"

    scripts = soup.find_all("script")

    media_type = None
    showposters = []
    movieposters = []
    collectionposters = []
    mediux_filters = get_mediux_filters()
    title = None

    for script in scripts:
        if "files" in script.text:
            if "set" in script.text:
                if "Set Link\\" not in script.text:
                    data_dict = parse_string_to_dict(script.text)
                    poster_data = data_dict["set"]["files"]

    for data in poster_data:
        if (data["show_id"] is not None or data["show_id_backdrop"] is not None or data["episode_id"] is not None or data["season_id"] is not None or data["show_id"] is not None ):
            media_type = "Show"
        else:
            media_type = "Movie"

    for data in poster_data:
        if media_type == "Show":
            episodes = data_dict["set"]["show"]["seasons"]
            show_name = data_dict["set"]["show"]["name"]
            try:
                year = int(data_dict["set"]["show"]["first_air_date"][:4])
            except:
                year = None

            if data["fileType"] == "title_card":
                episode_id = data["episode_id"]["id"]
                season = data["episode_id"]["season_id"]["season_number"]
                season_data = [ episode for episode in episodes if episode["season_number"] == season][0]
                episode_data = [ episode for episode in season_data["episodes"] if episode["id"] == episode_id][0]
                episode = episode_data["episode_number"]
                file_type = "title_card"
            elif data["fileType"] == "backdrop":
                season = "Backdrop"
                episode = None
                file_type = "background"
            elif data["season_id"] is not None:
                season_id = data["season_id"]["id"]
                season_data = [episode for episode in episodes if episode["id"] == season_id][0]
                episode = "Cover"
                season = season_data["season_number"]
                file_type = "season_cover"
            elif data["show_id"] is not None:
                season = "Cover"
                episode = None
                file_type = "show_cover"

        elif media_type == "Movie":
            if data["movie_id"]:
                if data_dict["set"]["movie"]:
                    title = data_dict["set"]["movie"]["title"]
                    year = int(data_dict["set"]["movie"]["release_date"][:4])
                elif data_dict["set"]["collection"]:
                    movie_id = data["movie_id"]["id"]
                    movies = data_dict["set"]["collection"]["movies"]
                    movie_data = [movie for movie in movies if movie["id"] == movie_id][0]
                    title = movie_data["title"]
                    year = int(movie_data["release_date"][:4])
            elif data["collection_id"]:
                title = data_dict["set"]["collection"]["collection_name"]

        image_stub = data["id"]
        poster_url = f"{base_url}{image_stub}{quality_suffix}"

        if media_type == "Show":
            showposter = {}
            showposter["title"] = show_name
            showposter["season"] = season
            showposter["episode"] = episode
            showposter["url"] = poster_url
            showposter["source"] = "mediux"
            showposter["year"] = year

            if check_mediux_filter(mediux_filters=mediux_filters, filter=file_type):
                showposters.append(showposter)
            else:
                print(f"{show_name} - skipping. '{file_type}' is not in 'mediux_filters'")

        elif media_type == "Movie":
            if title:
                if "Collection" in title:
                    collectionposter = {}
                    collectionposter["title"] = title
                    collectionposter["url"] = poster_url
                    collectionposter["source"] = "mediux"
                    collectionposters.append(collectionposter)

                else:
                    movieposter = {}
                    movieposter["title"] = title
                    movieposter["year"] = int(year)
                    movieposter["url"] = poster_url
                    movieposter["source"] = "mediux"
                    movieposters.append(movieposter)

    return movieposters, showposters, collectionposters


def process_boxset_url(boxset_id, soup2):
    boxset_url = f"https://mediux.pro/boxsets/{boxset_id}"
    print(f"Fetching boxset data from: {boxset_url}")

    scripts = soup2.find_all("script")
    data_dict = {}

    for script in scripts:
        script_text = script.text
        if "files" in script_text and "set" in script_text and "Set Link\\" not in script_text:
            data_dict = parse_string_to_dict(script_text)
            break

    if not data_dict.get("boxset", {}).get("sets"):
        print("No relevant data found or invalid structure.")
        return []

    set_ids = [item["id"] for item in data_dict["boxset"]["sets"]]
    print(f"Extracted set IDs: {set_ids}")

    results = []
    for set_id in set_ids:
        try:
            set_results = set_posters(f"https://mediux.pro/sets/{set_id}")
            if set_results:
                results.extend(set_results)
        except Exception as e:
            print(f"Error processing set {set_id}: {e}")

    return results


def scrape(url):
    print(f"Processing URL: {url}")

    if "theposterdb.com" in url:
        if "/set/" in url:
            soup = cook_soup(url)
            return scrape_posterdb(soup)
        elif "/user/" in url:
            soup = cook_soup(url)
            return scrape_entire_user(soup)
        elif "/poster/" in url:
            soup = cook_soup(url)
            set_url = scrape_posterdb_set_link(soup)
            if set_url:
                set_soup = cook_soup(set_url)
                return scrape_posterdb(set_soup)
            else:
                sys.exit("Poster set not found. Check the link you are inputting.")
        else:
            sys.exit("Invalid ThePosterDB URL. Check the link you are inputting.")

    elif "mediux.pro" in url:
        if "/boxsets/" in url:
            print("Detected Mediux Boxset URL.")
            boxset_id = url.split("/")[-1]
            soup = cook_soup(url)
            return process_boxset_url(boxset_id, soup)
        elif "/user/" in url:
            soup = cook_soup(url)
            return scrape_mediux_user(soup)
        elif "/sets/" in url:
            print("Detected Mediux Set URL.")
            soup = cook_soup(url)
            return scrape_mediux(soup)
        else:
            sys.exit("Invalid Mediux URL. Check the link you are inputting.")

    elif ".html" in url:
        print("Detected local HTML file.")
        with open(url, "r", encoding="utf-8") as file:
            html_content = file.read()
        soup = BeautifulSoup(html_content, "html.parser")
        return scrape_posterdb(soup)

    else:
        sys.exit("Invalid URL. Check the link you are inputting.")


# Checks if url does not start with "//", "#", or is blank
def is_not_comment(url):
    regex = r"^(?!\/\/|#|$).+"
    pattern = re.compile(regex)
    return bool(pattern.match(url))


def parse_urls(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            urls = [url.strip() for url in file if is_not_comment(url.strip())]

        for url in urls:
            lower_url = url.lower()
            if "/user/" in lower_url:
                if "theposterdb.com" in lower_url:
                    scrape_entire_user(url)
                elif "mediux.pro" in lower_url:
                    scrape_mediux_user(url)
            else:
                set_posters(url)

    except FileNotFoundError:
        print("File not found. Please enter a valid file path.")


def scrape_entire_user(url):
    soup = cook_soup(url)
    pages = scrape_posterd_user_info(soup)

    base_url = url.split("?")[0]
    for page in range(1, pages + 1):
        print(f"Scraping page {page}.")
        page_url = f"{base_url}?section=uploads&page={page}"
        set_posters(page_url)


def scrape_mediux_user(url):
    print(f"Attempting to scrape '{url}' ...please be patient.")
    
    pages = scrape_mediux_user_info(url)
    if pages is None:
        print("Error retrieving page count.")
        return

    print(f"Found {pages} pages for '{url}'")
    base_url = url.split("?")[0]
    
    all_set_ids, all_boxset_ids = [], []
    for page in range(1, pages + 1):
        print(f"Scraping page {page}.")
        page_url = f"{base_url}?page={page}"
        page_soup = cook_soup(page_url)
        
        set_ids, boxset_ids = extract_ids_from_script(page_soup)
        all_set_ids.extend(set_ids)
        all_boxset_ids.extend(boxset_ids)

    unique_set_ids = list(set(all_set_ids))
    unique_boxset_ids = list(set(all_boxset_ids))
    
    print("Processing Sets:", unique_set_ids)
    print("Processing Box Sets:", unique_boxset_ids)
    process_ids(unique_set_ids, unique_boxset_ids)


def extract_ids_from_script(soup):
    scripts = soup.find_all("script")
    data_dict = {}

    for script in scripts:
        if "files" in script.text and "set" in script.text and "Set Link\\" not in script.text:
            data_dict = parse_string_to_dict(script.text)
            break  # Stop searching after finding the relevant script

    if not data_dict:
        print("No relevant script data found.")
        return [], []

    def find_key(data, key):
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for value in data.values():
                result = find_key(value, key)
                if result:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = find_key(item, key)
                if result:
                    return result
        return None

    sets = find_key(data_dict, "sets")

    if not sets:
        print("No 'sets' key found in the nested structure.")
        return [], []

    set_ids, boxset_ids = set(), set()

    for item in sets:
        if 'boxset' in item and item['boxset']:
            if item['boxset'].get('id'):
                boxset_ids.add(boxset_id)
        elif 'id' in item:
            set_ids.add(item['id'])

    return list(set_ids), list(boxset_ids)


def process_ids(set_ids, boxset_ids):
    for boxset_id in boxset_ids:
        url = f"https://mediux.pro/boxsets/{boxset_id}"
        set_posters(url)

    for set_id in set_ids:
        url = f"https://mediux.pro/sets/{set_id}"
        set_posters(url)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    
    plex_setup()

    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        # Handle 'bulk' command
        if command == "bulk":
            if len(sys.argv) > 2:
                file_path = sys.argv[2]
                parse_urls(file_path)
            else:
                print("Please provide the path to the .txt file.")
        
        elif "/user/" in command:
            if "theposterdb.com" in command:
                scrape_entire_user(command)
            elif "mediux.pro" in command:
                scrape_mediux_user(command)
        
        else:
            set_posters(command)

    else:
        while True:
            user_input = input(
                "Enter a ThePosterDB set (or user) or a MediUX set URL, or type 'stop' to exit: "
            ).strip()
            
            # Exit the loop if user inputs 'stop'
            if user_input.lower() == "stop":
                print("Stopping...")
                break
            
            # Handle 'bulk' command for user input
            elif user_input.lower() == "bulk":
                file_path = input("Enter the path to the .txt file: ").strip()
                try:
                    with open(file_path, "r") as file:
                        urls = file.readlines()
                    for url in urls:
                        url = url.strip()
                        set_posters(url)
                except FileNotFoundError:
                    print("File not found. Please enter a valid file path.")
            
            # Handle URLs for individual scraping or poster setting
            elif "/user/" in user_input.lower():
                if "theposterdb.com" in user_input.lower():
                    scrape_entire_user(user_input)
                elif "mediux.pro" in user_input.lower():
                    scrape_mediux_user(user_input)
            
            else:
                set_posters(user_input)