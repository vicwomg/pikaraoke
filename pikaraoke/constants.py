LANGUAGES = {
    "en": "English",
    "de_DE": "German",
    "es_VE": "Spanish (Venezuela)",
    "fi_FI": "Finnish",
    "fr_FR": "French",
    "id_ID": "Indonesian",
    "it_IT": "Italian",
    "ja_JP": "Japanese",
    "ko_KR": "Korean",
    "nl_NL": "Dutch",
    "nb_NO": "Norwegian",
    "pt_BR": "Brazilian Portuguese",
    "ru_RU": "Russian",
    "th_TH": "Thai",
    "zh_Hans_CN": "Chinese (Simplified)",
    "zh_Hant_TW": "Chinese (Traditional)",
}

ITUNES_COUNTRIES = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "BR": "Brazil",
    "CN": "China",
    "DE": "Germany",
    "FI": "Finland",
    "FR": "France",
    "ID": "Indonesia",
    "IT": "Italy",
    "JP": "Japan",
    "KR": "South Korea",
    "NL": "Netherlands",
    "NO": "Norway",
    "RU": "Russia",
    "TH": "Thailand",
    "TW": "Taiwan",
    "VE": "Venezuela",
}

# Page sizes offered by both the Settings default and the per-device dropdown on
# the Songs page, so the two controls always agree on what is choosable.
PER_PAGE_SIZES = [20, 50, 100, 250, 500]


def per_page_options(current: int) -> list[int]:
    """The sizes on offer, always including the one in force.

    Earlier versions stored any number, so a saved 75 has to stay selectable --
    a dropdown that cannot show it would silently save something else instead.
    """
    return sorted(set(PER_PAGE_SIZES) | {current})
