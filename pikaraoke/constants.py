import os
import sys

LANGUAGES = {
    "en": "English",
    "de_DE": "German",
    "es_VE": "Spanish (Venezuela)",
    "fi_FI": "Finnish",
    "fr_FR": "French",
    "it_IT": "Italian",
    "ja_JP": "Japanese",
    "ko_KR": "Korean",
    "nl_NL": "Dutch",
    "no_NO": "Norwegian",
    "pt_BR": "Brazilian Portuguese",
    "ru_RU": "Russian",
    "th_TH": "Thai",
    "zh_Hans_CN": "Chinese (Simplified)",
    "zh_Hant_TW": "Chinese (Traditional)",
}

def get_data_directory():
    """
    Returns the writable data directory for the application.
    Windows: %APPDATA%/pikaraoke
    Linux/Mac: ~/.pikaraoke
    """
    if sys.platform == 'win32':
        # Result: C:\Users\Username\AppData\Roaming\pikaraoke
        base_path = os.environ.get('APPDATA')
        path = os.path.join(base_path, 'pikaraoke')
    else:
        # Result: /home/username/.pikaraoke
        path = os.path.expanduser('~/.pikaraoke')

    # Ensure the directory exists
    if not os.path.exists(path):
        os.makedirs(path)
        
    return path