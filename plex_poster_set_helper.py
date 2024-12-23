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
    "collection": 18,
}

# Plex Configuration settings
base_url = ""
token = ""

# Directories
assets_directory = "assets"
asset_folders = True

# App settings
append_label = ["Overlay"]
overwrite_existing_assets = False
overwrite_labelled_shows = False
only_process_new_assets = True
useragent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# Data containers
tv = []
movies = []
plex_collections = []


def plex_setup():
    global tv, movies, plex_collections, append_label, overwrite_labelled_shows, assets_directory, overwrite_existing_assets, base_url, token, asset_folders, only_process_new_assets, useragent

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
            plex_collections = config.get("plex_collections")
            append_label = config.get("append_label", "Overlay")
            append_label = [append_label] if isinstance(append_label, str) else (append_label if isinstance(append_label, list) else ["Overlay"])
            assets_directory = config.get("assets_directory", "assets")
            
            # Only set the following variables if they were not overridden by command-line arguments
            if 'overwrite_existing_assets' not in globals() or overwrite_existing_assets is None:
                overwrite_existing_assets = config.get("overwrite_existing_assets", False)
            if 'overwrite_labelled_shows' not in globals() or overwrite_labelled_shows is None:
                overwrite_labelled_shows = config.get("overwrite_labelled_shows", False)
            if 'only_process_new_assets' not in globals() or only_process_new_assets is None:
                only_process_new_assets = config.get("only_process_new_assets", True)

            asset_folders = config.get("asset_folders", True)
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
        plex_collections = tv + movies
    else:
        handle_plex_exception(e, f"No config.json file found")

def find_collection(libraries, poster):
    collections = []

    for lib in libraries:
        try:
            # Get all collections from the library
            all_collections = lib.collections()
            for collection in all_collections:
                if collection.title.lower().replace(' collection', '') == poster['title'].lower().replace(' collection', ''):
                    collections.append(collection)
                    break  # No need to check other titles for this collection

        except Exception as e:
            # Log the exception with a message
            print(f"Error retrieving collections from library '{lib.title}': {e}")
            continue  # Continue processing other libraries

    return collections if collections else None

        
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
            #print(f"Directory created: {directory_path}")
        except OSError as e:
            print(f"Failed to create directory: {e}")


def save_to_assets_directory(assets_dir, plex_folder, file_name, file_url):
    file_path = get_asset_file_path(assets_dir, plex_folder, file_name)

    # Check if file exists and handle overwriting
    if os.path.exists(file_path):
        if not overwrite_existing_assets:
            print(f"File already exists and overwriting is disabled: {file_path}")
            return file_path
        #print(f"Overwriting existing file: {file_path}")

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
        
        #print(f"File downloaded and saved to: {file_path}")
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
    # Retrieve existing labels for the item
    existing_labels = [label.tag for label in library_item.labels]

    # Add new labels that do not already exist
    new_labels = [label for label in append_label if label not in existing_labels]

    if new_labels:
        try:
            # Add each new label individually
            for label in new_labels:
                library_item.addLabel(label)
            library_item.reload()  # Refresh the item's data after editing
            #print(f"Labels {new_labels} added to item '{library_item.title}'.")
        except Exception as e:
            print(f"Error adding labels to item '{library_item.title}': {e}")


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
    media_type_map = {'Show': 'tvdb', 'Movies': 'tmdb'}

    for lib in libraries:
        try:
            library_items = []

            # Check if the source is 'mediux' and required fields are present
            if poster.get("source") == "mediux" and all(poster.get(key) for key in ["media_type", "id"]):
                # Determine GUID prefix based on media type
                guid_prefix = media_type_map.get(poster["media_type"])
                if guid_prefix:
                    library_items = lib.search(guid=f"{guid_prefix}://{poster['id']}")

            # If items are found, return the first match
            if library_items:
                library_item = library_items[0]
                show_path = get_file_path_from_plex(library_item.ratingKey)
                return library_item, show_path

            # Fallback to searching by title and year if ID search is not available or fails
            kwargs = {'year': poster.get("year")} if poster.get("year") else {}
            library_item = lib.get(poster["title"], **kwargs)

            if library_item:
                show_path = get_file_path_from_plex(library_item.ratingKey)
                return library_item, show_path

        except Exception as e:
            error_message = str(e)
            if "Unable to find item with title" not in error_message:
                print(e)

    return None, None


def check_label_for_item(rating_key):
    headers = {"X-Plex-Token": token}
    url = f"{base_url}/library/metadata/{rating_key}"

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.content)

        # Extract labels
        labels = {label.get("tag").strip() for label in root.findall(".//Label") if label.get("tag")}

        # Check if any of the labels in append_label exist in the labels
        return any(label in labels for label in append_label)

    except requests.RequestException as e:
        print(f"Error checking label for item with rating key {rating_key}: {e}")
    except ET.ParseError as e:
        print(f"Failed to parse XML response for rating key {rating_key}: {e}")

    return False


def upload_tv_poster(poster, tv):
    tv_show, show_path = find_in_library(tv, poster)
    
    if tv_show is None or show_path is None:
        print(f"{poster['title']} not found in TV libraries or failed to load path.")
        return
    
    if check_label_for_item(tv_show.ratingKey) and not overwrite_labelled_shows:
        #print(f"Skipping upload for {poster['title']} as it already has the label '{append_label}'.")
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
            #print(f"Skipping upload for {poster['title']} as only processing new assets.")
            return
        #print(f"Using existing file for upload to {poster['title']}.")
    else:
        file_path = save_to_assets_directory(assets_directory, asset_path, file_name, poster["url"])
        if file_path is None:
            print(f"Skipping upload for {poster['title']} due to download error.")
            return
    try:
        if poster["season"] in {"Cover", "Backdrop"}:
            upload_target = tv_show
            print(f"Uploading art for {poster['title']} - {poster['season']}.")
        elif poster["season"] == 0:
            try:
                upload_target = tv_show.season("Specials")
                print(f"Uploading art for {poster['title']} - Specials.")
                if poster["episode"] not in {"Cover", None}:
                    upload_target = upload_target.episode(poster["episode"])
                    print(f"Uploading art for {poster['title']} - Specials Episode {poster['episode']}.")
            except Exception:
                #print(f"Episode {poster['episode']} not found in Season {poster['season']} for {poster['title']}. Skipping upload.")
                return 
        elif poster["season"] >= 1:
            try:
                season = tv_show.season(poster["season"])
                if poster["episode"] in {"Cover", None}:
                    upload_target = season
                    print(f"Uploading art for {poster['title']} - Season {poster['season']}.")
                else:
                    upload_target = season.episode(poster["episode"])
                    print(f"Uploading art for {poster['title']} - Season {poster['season']} Episode {poster['episode']}.")
            except Exception:
                #print(f"Episode {poster['episode']} not found in Season {poster['season']} for {poster['title']}. Skipping upload.")
                return
        else:
            print(f"Skipping upload for {poster['url']} due to sorting error.")
            return

        # Upload art
        try:
            if poster["season"] == "Backdrop":
                upload_target.uploadArt(filepath=file_path)
                upload_target.lockArt()
            else:
                upload_target.uploadPoster(filepath=file_path)
                upload_target.lockPoster()
        except Exception as e:
            print(f"Unable to upload art for {poster['title']}. Error: {e}")
            return
        
        # Add labels and delay
        add_label_rating_key(tv_show)
        time.sleep(6)
    except Exception as e:
        print(f"Error uploading {poster['title']} - {e}")



def upload_movie_poster(poster, movies):
    movies, show_path = find_in_library(movies, poster)
    if not movies or not show_path:
        print(f"{poster['title']} not found in movies libraries or failed to load path.")
        return
        
    for movie in movies:
        if check_label_for_item(movie.ratingKey) and not overwrite_labelled_shows:
            #print(f"Skipping upload for {poster['title']} as it already has the label '{append_label}'.")
            break
        
        # Determine asset type
        asset_type = "poster" if poster.get("source") == "posterdb" else {"poster": "poster", "background": "background", "backdrop": "background"}.get(poster.get("file_type"), "poster")

        if asset_type in {"poster", "background"}:
            asset_path = f"movies/{show_path}" if asset_folders else f"movies"
            file_name = f"{asset_type}.jpg" if asset_folders else f"{show_path}_{asset_type}.jpg"
        else:
            print(f"Unknown asset type '{asset_type}' for {poster['title']}.")
            break

    
        file_path = get_asset_file_path(assets_directory, asset_path, file_name)
        
        if os.path.exists(file_path) and not overwrite_existing_assets:
            if only_process_new_assets:
                #print(f"Skipping upload for {poster['title']} as the {asset_type} already exists.")
                break
            #print(f"Using existing file for upload to {poster['title']}.")
        else:
            file_path = save_to_assets_directory(assets_directory, asset_path, file_name, poster["url"])
            if not file_path:
                #print(f"Skipping upload for {poster['title']} due to download error.")
                break
                
        try:
            if asset_type == 'poster':
                movie.uploadPoster(filepath=file_path)
                movie.lockPoster()
            elif asset_type == 'background':
                movie.uploadArt(filepath=file_path)
                movie.lockArt()
            else:
                print(f"Unknown asset type '{asset_type}' for {poster['title']}.")
                break
            print(f'Uploaded {asset_type} for {poster["title"]}.')
                
            # Add labels to the collection item after upload
            add_label_rating_key(movie)
                
            time.sleep(6)  # Timeout to prevent spamming servers
        except Exception as e:
            print(f'Unable to upload {asset_type} for {poster["title"]}. Error: {e}')
            break


def upload_collection_poster(poster, plex_collections):
    # Use find_collection to get matching collections
    collection_items = find_collection(plex_collections, poster)
    
    if not collection_items:
        print(f"No collections found for {poster['title']}.")
        return

    item_found = False
    for item in collection_items:
        if item.title.lower().replace(' collection', '') == poster['title'].lower().replace(' collection', ''):
            item_found = True

            if check_label_for_item(item.ratingKey) and not overwrite_labelled_shows:
                #print(f"Skipping upload for {poster['title']} as it already has the label '{append_label}'.")
                break

            # Determine asset type
            asset_type = ("poster" if poster.get("source") == "posterdb"  else {"poster": "poster", "background": "background", "backdrop": "background"}.get(poster.get("file_type"), "poster") )

            if asset_type in {"poster", "background"}:
                asset_path = f"collections/{poster['title']}" if asset_folders else "collections"
                file_name = f"{asset_type}.jpg" if asset_folders else f"{poster['title']}_{asset_type}.jpg"
            else:
                print(f"Unknown asset type '{asset_type}' for {poster['title']}.")
                break

            file_path = get_asset_file_path(assets_directory, asset_path, file_name)
            
            # Check if the asset already exists
            if os.path.exists(file_path) and not overwrite_existing_assets:
                if only_process_new_assets:
                    #print(f"Skipping upload for {poster['title']} as the {asset_type} already exists.")
                    break
                #print(f"Using existing file for upload to {poster['title']}.")
            else:
                file_path = save_to_assets_directory(assets_directory, asset_path, file_name, poster["url"])
                if not file_path:
                    print(f"Skipping upload for {poster['title']} due to download error.")
                    break
            
            try:
                # Upload the poster or background to the collection
                if asset_type == 'poster':
                    item.uploadPoster(filepath=file_path)
                    item.lockPoster()
                elif asset_type == 'background':
                    item.uploadArt(filepath=file_path)
                    item.lockArt()
                else:
                    print(f"Unknown asset type '{asset_type}' for {poster['title']}.")
                    break
                print(f'Uploaded {asset_type} for {poster["title"]}.')
                
                # Add labels to the collection item after upload
                add_label_rating_key(item)
                
                time.sleep(6)  # Timeout to prevent spamming servers
            except Exception as e:
                print(f'Unable to upload {asset_type} for {poster["title"]}. Error: {e}')
            break

    if not item_found:
        print(f"Item with title '{poster['title']}' not found in collections.")


def set_posters(url):
    result = scrape(url)

    if not result or len(result) != 3:
        #print("Scrape function did not return the expected 3 values.")
        return

    movieposters, showposters, collectionposters = result

    if not any([movieposters, showposters, collectionposters]):
        #print("No posters found.")
        return

    for poster in collectionposters:
        upload_collection_poster(poster, plex_collections)

    for poster in movieposters:
        upload_movie_poster(poster, movies)

    for poster in showposters:
        upload_tv_poster(poster, tv)



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
        #print(f"Processing page: {current_page}")
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
    base_url = "https://mediux.pro/_next/image?url=https%3A%2F%2Fapi.mediux.pro%2Fassets%2F"
    quality_suffix = "&w=3840&q=80"

    scripts = soup.find_all("script")

    media_type = None
    showposters = []
    movieposters = []
    collectionposters = []
    mediux_filters = get_mediux_filters()
    title = None
    poster_data = []

    # Extract and parse the poster data from the script tags
    for script in scripts:
        if "files" in script.text and "set" in script.text and "Set Link\\" not in script.text:
            data_dict = parse_string_to_dict(script.text)
            poster_data = data_dict.get("set", {}).get("files", [])

    # Determine media type based on the presence of specific IDs
    for data in poster_data:
        if (data.get("show_id") or data.get("show_id_backdrop") or 
            data.get("episode_id") or data.get("season_id") or 
            data.get("show_id")):
            media_type = "Show"
        else:
            media_type = "Movie"

    # Process each poster data entry
    for data in poster_data:
        image_stub = data.get("id")
        poster_url = f"{base_url}{image_stub}{quality_suffix}"

        if media_type == "Show":
            episodes = data_dict["set"].get("show", {}).get("seasons", [])
            show_name = data_dict["set"].get("show", {}).get("name", "Unknown")
            show_tvdb_id = data_dict["set"].get("show", {}).get("tvdb_id")
            first_air_date = data_dict["set"]["show"].get("first_air_date", "0000")
            year = int(first_air_date.split('-')[0] if first_air_date and '-' in first_air_date else first_air_date[:4]) if first_air_date else 0000


            if data.get("fileType") == "title_card":
                file_type = "title_card"
                episode_id = data.get("episode_id", {}).get("id")
                season = data.get("episode_id", {}).get("season_id", {}).get("season_number")
                season_data = next((ep for ep in episodes if ep.get("season_number") == season), {})
                episode_data = next((ep for ep in season_data.get("episodes", []) if ep.get("id") == episode_id), {})
                episode = episode_data.get("episode_number", "")
                if not episode:
                    title = data.get("title", "")
                    if not title:
                        print(f"{show_name} - Error getting episode info for a title card.")
                    else:
                        match = re.search(r"E(\d{1,2})", title)
                        if match:
                            episode = int(match.group(1))
                        else:
                            print(f"{show_name} - Error parsing title card info from title: {title}")
            elif data.get("fileType") == "backdrop":
                season = "Backdrop"
                episode = None
                file_type = "background"
            elif data.get("season_id"):
                season_id = data.get("season_id", {}).get("id")
                season_data = next((ep for ep in episodes if ep["id"] == season_id), {})
                episode = "Cover"
                season = season_data.get("season_number")
                file_type = "season_cover"
            elif data.get("show_id"):
                season = "Cover"
                episode = None
                file_type = "show_cover"

            showposter = {
                "media_type": media_type,
                "title": show_name,
                "id": show_tvdb_id,
                "season": season,
                "episode": episode,
                "url": poster_url,
                "source": "mediux",
                "year": year
            }

            if check_mediux_filter(mediux_filters=mediux_filters, filter=file_type):
                showposters.append(showposter)
            #else:
                #print(f"{show_name} - skipping. '{file_type}' is not in 'mediux_filters'")

        elif media_type == "Movie":
            if data.get("movie_id"):
                if data.get("movie_id").get("id"):
                    movie_id = data.get("movie_id", {}).get("id")
                    if data_dict["set"].get("movie"):
                        title = data_dict["set"]["movie"].get("title", "Unknown")
                        release_date = data_dict["set"]["movie"].get("release_date", "0000")
                        year = int(release_date.split('-')[0]) if release_date and '-' in release_date else int(release_date[:4]) if release_date else 0000
                    elif data_dict["set"].get("collection"):
                        movies = data_dict["set"]["collection"].get("movies", [])
                        movie_data = next((movie for movie in movies if movie["id"] == movie_id), {})
                        title = movie_data.get("title", "Unknown")
                        release_date = movie_data.get("release_date", "0000")
                        year = int(release_date.split('-')[0]) if release_date and '-' in release_date else int(release_date[:4]) if release_date else 0000
                    else:
                        return
                    movieposter = {
                        "media_type": media_type,
                        "title": title,
                        "id": movie_id,
                        "year": int(year),
                        "url": poster_url,
                        "source": "mediux",
                        "file_type": "poster"
                    }
                    movieposters.append(movieposter)
                    # Check and add movie backdrop
                    for file in data_dict["set"].get("files", []):
                        movie_id_backdrop = file.get("movie_id_backdrop")
                        if movie_id_backdrop and isinstance(movie_id_backdrop, dict):
                            backdrop_id = movie_id_backdrop.get("id")
                            if backdrop_id and backdrop_id == movie_id:
                                backdrop_url = f"{base_url}{file['id']}{quality_suffix}"
                                movieposter_background = {
                                    "media_type": media_type,
                                    "title": title,
                                    "id": movie_id,
                                    "url": backdrop_url,
                                    "source": "mediux",
                                    "file_type": "background"
                                }
                                movieposters.append(movieposter_background)
            if data.get("collection_id"):
                if data.get("collection_id").get('id'):
                    collection_id = data.get("collection_id").get("id")
                    title = data_dict["set"]["collection"].get("collection_name", "Unknown")
                    if "Collection" in title:
                        collectionposter = {
                            "media_type": "Collection",
                            "title": title,
                            "url": poster_url,
                            "source": "mediux",
                            "file_type": "poster"
                        }
                        collectionposters.append(collectionposter)
                        if 'backdropCheck' in data_dict["set"] and data_dict["set"]['backdropCheck']:
                            for backdrop in data_dict["set"]['backdropCheck']:
                                backdrop_id = backdrop['id']
                                backdrop_url = f"{base_url}{backdrop_id}{quality_suffix}"
                                collectionposter_background = {
                                    "media_type": "Collection",
                                    "title": title,
                                    "url": backdrop_url,
                                    "source": "mediux",
                                    "file_type": "background"
                                }
                                collectionposters.append(collectionposter_background)                      
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
    #print(f"Extracted set IDs: {set_ids}")

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
            #print("Detected Mediux Boxset URL.")
            boxset_id = url.split("/")[-1]
            soup = cook_soup(url)
            return process_boxset_url(boxset_id, soup)
        elif "/user/" in url:
            soup = cook_soup(url)
            return scrape_mediux_user(soup)
        elif "/sets/" in url:
            #print("Detected Mediux Set URL.")
            soup = cook_soup(url)
            return scrape_mediux(soup)
        else:
            sys.exit("Invalid Mediux URL. Check the link you are inputting.")

    elif ".html" in url:
        #print("Detected local HTML file.")
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
        #print(f"Scraping page {page}.")
        page_url = f"{base_url}?section=uploads&page={page}"
        set_posters(page_url)


def scrape_mediux_user(url):
    #print(f"Attempting to scrape '{url}' ...please be patient.")
    base_url = url.split('?')[0]
    
    if not base_url.endswith('/sets'):
        base_url = base_url.rstrip('/') + '/sets'

    pages = scrape_mediux_user_info(base_url)
    if pages is None:
        print("Error retrieving page count.")
        return

    #print(f"Found {pages} pages for '{base_url}'")
    all_set_ids, all_boxset_ids = [], []
    for page in range(1, pages + 1):
        page_url = f"{base_url}?page={page}"
        #print(f"Scraping page {page}.")
        page_soup = cook_soup(page_url)
        
        set_ids, boxset_ids = extract_ids_from_script(page_soup)
        all_set_ids.extend(set_ids)
        all_boxset_ids.extend(boxset_ids)

    unique_set_ids = list(set(all_set_ids))
    unique_boxset_ids = list(set(all_boxset_ids))
    
    #print("Processing Sets:", unique_set_ids)
    #print("Processing Box Sets:", unique_boxset_ids)
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
                boxset_ids.add(item['boxset']['id'])
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
    # Initialize indices for cleanup later
    oe_index = ol_index = na_index = None

    # Parse command-line arguments for flags
    if "--OE" in sys.argv:
        oe_index = sys.argv.index("--OE") + 1
        if oe_index < len(sys.argv):
            overwrite_existing_assets = sys.argv[oe_index].lower() == "true"
    
    if "--OL" in sys.argv:
        ol_index = sys.argv.index("--OL") + 1
        if ol_index < len(sys.argv):
            overwrite_labelled_shows = sys.argv[ol_index].lower() == "true"
    
    if "--NA" in sys.argv:
        na_index = sys.argv.index("--NA") + 1
        if na_index < len(sys.argv):
            only_process_new_assets = sys.argv[na_index].lower() == "true"
    
    # Clean up sys.argv to remove processed flags and values
    indices_to_remove = {i for i in [oe_index, ol_index, na_index] if i is not None}
    sys.argv = [arg for i, arg in enumerate(sys.argv) if i not in indices_to_remove and arg not in ["--OE", "--OL", "--NA"]]

    # Initialize Plex setup
    plex_setup()
    
    # Check for command input
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
        # Interactive mode
        while True:
            user_input = input("Enter a ThePosterDB set (or user) or a MediUX set URL, or type 'stop' to exit: ").strip()
            
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
