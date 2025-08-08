from enum import Enum, StrEnum
from PySide6.QtGui import QIcon
from dotenv import load_dotenv
import os

load_dotenv("secrets.env")

ON = True
OFF = False


class Const(StrEnum):
    API_KEY = str(os.getenv("API_KEY"))
    API_REGION = str(os.getenv("API_REGION"))
    MAIN_CHANNEL = str(os.getenv("MAIN_CHANNEL"))
    ALT_CHANNEL = str(os.getenv("ALT_CHANNEL"))
    TTS_FILE = "output.wav"
    MACRO_FILE = "macro/$.wav"
    ICON_FILE = "icons/$.png"
    CUSTOM_FILE = "macro/cust_macro.wav"
    LABEL = "label"
    WIDTH = "width"
    PHRASE = "phrase"
    CUSTOM = "custom"
    REPEAT = "repeat"
    REPLACE = "$"


class OBS(StrEnum):
    HOST = str(os.getenv("OBS_HOST"))
    PORT = str(os.getenv("OBS_PORT"))
    PWD = str(os.getenv("OBS_PWD"))
    AVA_SCENE = "Goblin"
    AVA_SOURCE = "Goblin-Talking"
    BUB_SCENE = "WoW"
    BUB_SOURCE = "Speech Bubble"


class Emotion(StrEnum):
    FRIENDLY = "Friendly"
    GENERAL = "General"
    ANGRY = "Angry"
    CHEERFUL = "Cheerful"
    EXCITED = "Excited"
    HOPEFUL = "Hopeful"
    SAD = "Sad"
    SHOUTING = "Shouting"
    TERRIFIED = "Terrified"
    UNFRIENDLY = "Unfriendly"
    WHISPERING = "Whispering"


class Voice(StrEnum):
    EN_JANE = "en-US-JaneNeural"
    EN_PRABHAT = "en-IN-PrabhatNeural"
    EN_NEERJA = "en-IN-NeerjaNeural"
    EN_DAVIS = "en-US-DavisNeural"
    RU_DMITRY = "ru-RU-DmitryNeural"
    RU_DARIYA = "ru-RU-DariyaNeural"
    FI_SELMA = "fi-FI-SelmaNeural"
    SV_SOFIE = "sv-SE-SofieNeural"
    DE_MAJA = "de-DE-MajaNeural"
    ES_VERA = "es-ES-VeraNeural"
    EN_SONIA = "en-GB-SoniaNeural"
    EN_MAISIE = "en-GB-MaisieNeural"
    NL_COLETTE = "nl-NL-ColetteNeural"


class RaidIcon(StrEnum):
    STAR = "star"
    CIRCLE = "circle"
    DIAMOND = "diamond"
    TRIANGLE = "triangle"
    MOON = "moon"
    SQUARE = "square"
    CROSS = "cross"
    SKULL = "skull"
    UNMARKED = "unmarked"


class WebSocketIcon:
    ON = QIcon("icons/wifi.svg")
    OFF = QIcon("icons/no_wifi.svg")
    OFF_RED = QIcon("icons/no_wifi_red.svg")


class PhraseMacro(Enum):
    YES = {
        Const.LABEL: "Yes",
        Const.WIDTH: 40,
        Const.PHRASE: "Yes",
    }
    NO = {
        Const.LABEL: "No",
        Const.WIDTH: 40,
        Const.PHRASE: "No",
    }
    OKAY = {
        Const.LABEL: "Ok",
        Const.WIDTH: 40,
        Const.PHRASE: "Okay",
    }
    CANTDO = {
        Const.LABEL: "Can't do that",
        Const.WIDTH: 90,
        Const.PHRASE: "I can't do that",
    }
    INTERRUPT = {
        Const.LABEL: "Interrupting",
        Const.WIDTH: 90,
        Const.PHRASE: "Interrupting",
    }
    NOINTERRUPT = {
        Const.LABEL: "No interrupt",
        Const.WIDTH: 90,
        Const.PHRASE: "I can't interrupt",
    }
    DISPEL = {
        Const.LABEL: "Dispel",
        Const.WIDTH: 60,
        Const.PHRASE: "Dispelling",
    }


FIXES = {
    "n;t": "n't",
}

PRONUNCIATIONS = {}
