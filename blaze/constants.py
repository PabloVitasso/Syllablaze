"""
Constants for the Syllablaze application.
"""

# Application name
APP_NAME = "Syllablaze"

# Application version
APP_VERSION = "0.2"

# Organization name
ORG_NAME = "KDE"

# GitHub repository URL
GITHUB_REPO_URL = "https://github.com/PabloVitasso/Syllablaze"

# Default whisper model
DEFAULT_WHISPER_MODEL = "tiny"

# No model information needed - using whisper._MODELS directly

# Valid language codes for Whisper
VALID_LANGUAGES = {
    'auto': 'Auto-detect',
    'en': 'English',
    'es': 'Spanish',
    'fr': 'French',
    'de': 'German',
    'it': 'Italian',
    'pt': 'Portuguese',
    'nl': 'Dutch',
    'pl': 'Polish',
    'ja': 'Japanese',
    'zh': 'Chinese',
    'ru': 'Russian',
    # Add more languages as needed
}