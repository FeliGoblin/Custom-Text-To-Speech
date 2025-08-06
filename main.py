import re
import sys
import vlc
import logging

from PySide6.QtGui import QFont, QFontDatabase, QIcon, QKeyEvent
from PySide6.QtCore import QSize, Qt, QEvent

from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QWidget,
    QSizePolicy,
    QHBoxLayout,
    QVBoxLayout,
    QMenu,
)
from azure.cognitiveservices.speech import (
    SpeechConfig,
    SpeechSynthesizer,
    Connection,
    SpeechSynthesisOutputFormat,
    AudioDataStream,
    ResultReason,
    CancellationReason,
)

from const import Const, OBS, Emotion, Voice, RaidIcon, PhraseMacro
from replace import FIXES, PRONUNCIATIONS
from obswebsocket import obsws, requests
from time import sleep

logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger("TTS")


class MainWindow(QMainWindow):
    """Application window."""

    def __init__(self, app):
        class Sized_QPushButton(QPushButton):
            def __init__(self, text: str, /, width: int, height: int = 30):
                super().__init__(text)
                self.setMinimumSize(width, height)
                self.setMaximumSize(width, height)

        class RaidIcon_QPushButton(Sized_QPushButton):
            def __init__(self, /, icon: str):
                super().__init__("", 30, 30)
                self.setIcon(QIcon(Const.ICON_FILE.replace(Const.REPLACE, icon, 1)))
                self.setIconSize(QSize(30, 30))

        class Sized_QLineEdit(QLineEdit):
            def __init__(self, /, width: int, height: int = 30):
                super().__init__()
                self.setMinimumSize(width, height)
                self.setMaximumSize(width, height)

        super().__init__()
        self.app = app
        self.player: vlc.MediaPlayer = vlc.MediaPlayer()
        self.custom_macro: str = None
        self.speech_synthesizer: SpeechSynthesizer = None
        self.websocket: obsws = None
        self.websocket_connected: bool = False
        self.input_text: QLineEdit = None
        self.btn_cust_macro: QPushButton = None
        self.tts_emotion: str = Emotion.FRIENDLY
        self.tts_voice: str = Voice.EN_JANE
        self.menu_emotion: QMenu = None
        self.menu_voice: QMenu = None
        self.startPos = None
        self.last_tts_text: str = ""
        self.html_template: str = ""
        self.avatar_item_id: int = None
        self.bubble_item_id: int = None

        self.setWindowTitle("Text to Speech")

        self.setup_synthesis()
        self.setup_websocket()
        QApplication.instance().installEventFilter(self)

        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        row_1 = QHBoxLayout()
        row_1.setSpacing(0)
        row_1.setContentsMargins(0, 0, 0, 0)
        row_2 = QHBoxLayout()
        row_2.setSpacing(0)
        row_2.setContentsMargins(0, 0, 0, 0)
        row_3 = QHBoxLayout()
        row_3.setSpacing(0)
        row_3.setContentsMargins(0, 0, 0, 0)

        # Raid Icon Macros
        for icon in RaidIcon:
            row_1.addWidget(action := RaidIcon_QPushButton(icon.value))
            action.clicked.connect(lambda _, i=icon.value: self.play_macro(i))

        # Spacer
        row_1.addWidget(QLabel(""))

        # Custom Macro
        row_1.addWidget(cust_macro := QPushButton("  Custom Macro  "))
        self.btn_cust_macro = cust_macro
        self.btn_cust_macro.clicked.connect(lambda: self.play_macro(Const.CUSTOM))

        # Phrases
        for phrase in PhraseMacro:
            row_2.addWidget(
                button := Sized_QPushButton(
                    phrase.value.get(Const.LABEL, ""), width=phrase.value.get(Const.WIDTH, 40)
                )
            )
            button.clicked.connect(lambda _, p=phrase.name.lower(): self.play_macro(p))

        # Text Input
        row_3.addWidget(input_text := Sized_QLineEdit(width=340))
        self.input_text = input_text
        placeholder_text = self.tts_voice.split("-")[0] + "-" + self.tts_voice.split("-")[-1]
        if "Neural" in placeholder_text:
            placeholder_text = placeholder_text.replace("Neural", "")
        self.input_text.setPlaceholderText(f" {self.tts_emotion}  -  {placeholder_text}")

        # Stop Button
        row_3.addWidget(btn_stop := Sized_QPushButton("Stop", width=50))
        btn_stop.clicked.connect(self.stop)

        # Repeat Button
        row_3.addWidget(btn_repeat := Sized_QPushButton("Repeat", width=60))
        btn_repeat.clicked.connect(lambda: self.play_macro(Const.REPEAT))

        layout.addLayout(row_1)
        layout.addLayout(row_2)
        layout.addLayout(row_3)
        widget = QWidget()
        widget.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(QSize(450, 90))
        self.setCentralWidget(widget)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.FramelessWindowHint)

        # Context Menu
        self.context_menu = QMenu(self)
        self.menu_emotion = self.context_menu.addMenu("Emotion")
        self.menu_voice = self.context_menu.addMenu("Voice")
        menu_cust_macro = self.context_menu.addAction("Set Custom Macro")
        menu_cust_macro.triggered.connect(self.set_custom_macro)
        menu_exit = self.context_menu.addAction("Exit")
        menu_exit.triggered.connect(self.app.exit)

        # Emotions
        for emotion in Emotion:
            action = self.menu_emotion.addAction(emotion.value)
            action.triggered.connect(lambda _, e=emotion.value: self.set_emotion(e))

        # Voices
        for voice in Voice:
            action = self.menu_voice.addAction(voice.value)
            action.triggered.connect(lambda _, v=voice.value: self.set_voice(v))

    async def Test(self):
        print("test")

    def stop(self) -> None:
        """Stop the player."""
        _LOGGER.info("Stopping the player")
        self.player.stop()

    def play(self, file: str, channel: str):
        """Play audio file."""
        media = vlc.Media(file)
        self.player.set_media(media)
        self.player.audio_output_device_set(None, channel)
        _LOGGER.info("Playing audio file (%s) on %s", file, Const(channel).name)
        self.player.play()

    def play_macro(self, macro: str) -> None:
        """Play the macro file."""
        if macro == Const.REPEAT:
            file = Const.TTS_FILE
            text = None
        elif macro == Const.CUSTOM:
            if not self.custom_macro:
                _LOGGER.warning("A custom macro has not been set")
                return
            file = Const.CUSTOM_FILE
            text = self.custom_macro
        else:
            file = Const.MACRO_FILE.replace(Const.REPLACE, macro, 1)
            try:
                text = RaidIcon(macro).value + Const.REPLACE
                _LOGGER.info("Macro is a raid icon")
            except ValueError:
                text = PhraseMacro[macro.upper()].value[Const.PHRASE]
                _LOGGER.info("Macro is a phrase")

        _LOGGER.info(f"Playing macro: {macro}")
        self.play(file, Const.MAIN_CHANNEL)
        self.avatar_talk(text)

    def set_lineEdit_placeholder(self) -> None:
        """Set the placeholder to show the selected Voice and Emotion."""
        voice = self.tts_voice.split("-")[0] + "-" + self.tts_voice.split("-")[-1]
        if "Neural" in voice:
            voice = voice.replace("Neural", "")

        placeholder = f" {self.tts_emotion} - {voice}"
        _LOGGER.info("Setting the line-edit placeholder to: %s", placeholder)
        self.input_text.setPlaceholderText(placeholder)

    def set_emotion(self, emotion: str) -> None:
        """Set the emotion for the TTS."""
        _LOGGER.info("Setting emotion to: %s", emotion)
        self.tts_emotion = emotion
        self.set_lineEdit_placeholder()

    def set_voice(self, voice: str) -> None:
        """Set the voice for the TTS."""
        _LOGGER.info("Setting voice to: %s", voice)
        self.tts_voice = voice
        self.set_lineEdit_placeholder()

    def move_mouth(self, enable: bool) -> None:
        """Enable and disable the open-mouth image of the avatar."""
        self.websocket.call(
            requests.SetSceneItemEnabled(
                **{
                    "sceneName": OBS.AVA_SCENE,
                    "sceneItemId": self.avatar_item_id,
                    "sceneItemEnabled": enable,
                }
            )
        )

    def send_speech_bubble_text(self, enable: bool, text: str = "") -> None:
        """Send TTS text to the speech-bubble browser source."""
        if text:
            icon = ""
            if text.endswith(Const.REPLACE):
                text = text[:-1]
                icon = f'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<img src="icons/{text}.png" />'
            html = self.html_template.replace(Const.REPLACE, text.capitalize() + icon, 1)
            with open("speech-bubble.html", "w") as f:
                f.write(html)
        self.websocket.call(
            requests.SetSceneItemEnabled(
                **{
                    "sceneName": OBS.BUB_SCENE,
                    "sceneItemId": self.bubble_item_id,
                    "sceneItemEnabled": enable,
                }
            )
        )

    def avatar_talk(self, override: str | None = None) -> None:
        """Make the on-screen avatar talk while the speech audio is playing."""
        if self.websocket_connected:
            sleep(0.2)
            self.send_speech_bubble_text(True, override or self.last_tts_text)
            self.move_mouth(True)
            while self.player.is_playing():
                self.move_mouth(False)
                sleep(0.2)
                self.move_mouth(True)
                sleep(0.2)
            self.move_mouth(False)
            # self.send_speech_bubble_text(False)

    def setup_websocket(self) -> None:
        """Set up websocket client and get scene item ID."""
        self.websocket = obsws(OBS.HOST, OBS.PORT)

        try:
            _LOGGER.info("Connecting to OBS Websocket")
            self.websocket.connect()
            avatar_scene_item = self.websocket.call(
                requests.GetSceneItemId(
                    **{"sceneName": OBS.AVA_SCENE, "sourceName": OBS.AVA_SOURCE}
                )
            )
            self.avatar_item_id = avatar_scene_item.datain["sceneItemId"]
            bubble_scene_item = self.websocket.call(
                requests.GetSceneItemId(
                    **{"sceneName": OBS.BUB_SCENE, "sourceName": OBS.BUB_SOURCE}
                )
            )
            self.bubble_item_id = bubble_scene_item.datain["sceneItemId"]
            self.websocket_connected = True
        except Exception:
            _LOGGER.warning("Couldn't connect to OBS. Is it open?")
            return

        with open("speech-bubble-template.html") as f:
            self.html_template = f.read()

    def setup_synthesis(self) -> None:
        """Configure and connect to Azure TTS."""
        speech_config = SpeechConfig(subscription=Const.API_KEY, region=Const.API_REGION)
        speech_config.set_speech_synthesis_output_format(
            SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )
        self.speech_synthesizer = SpeechSynthesizer(
            speech_config=speech_config, audio_config=None
        )
        _LOGGER.info("Connecting to Azure TTS")
        connection = Connection.from_speech_synthesizer(self.speech_synthesizer)
        connection.open(True)
        _LOGGER.info("Connected!")

    def text_to_speech(
        self, input_text: str, channel: str, *, create_custom_macro: bool = False
    ) -> None:
        """Synthesize speech and play it on selected channel.

        Optionally create the custom macro without playing it.
        """
        self.input_text.clear()
        tts_rate = "5"
        tts_pitch = "5"

        _LOGGER.info("Fixing typing mistakes")
        for mistake, fix in FIXES.items():
            input_text = re.sub(mistake, fix, input_text, flags=re.IGNORECASE)

        # Differentiate Azure TTS input from the written text.
        tts_input = input_text

        _LOGGER.info("Fixing pronunciations")
        for mispronounced, repronounced in PRONUNCIATIONS.items():
            tts_input = re.sub(mispronounced, repronounced, tts_input, flags=re.IGNORECASE)

        # SSML creation.
        tts_ssml = (
            '<speak xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="http://www.w3.org/2001/mstts" xmlns:emo="http://www.w3.org/2009/10/emotionml" version="1.0" xml:lang="en-US"><voice name="'
            + self.tts_voice
            + '"><mstts:express-as style="'
            + self.tts_emotion.lower()
            + '" styledegree="1"><prosody rate="'
            + tts_rate
            + '%" pitch="'
            + tts_pitch
            + '%">'
            + tts_input
            + "</prosody></mstts:express-as></voice></speak>"
        )

        try:
            _LOGGER.info("Synthesizing speech from text: %s", tts_input)
            tts_result = self.speech_synthesizer.speak_ssml_async(tts_ssml).get()

            # Some error checking.
            if tts_result.reason == ResultReason.SynthesizingAudioCompleted:
                _LOGGER.info("Speech synthesized!")
            elif tts_result.reason == ResultReason.Canceled:
                cancellation_details = tts_result.cancellation_details
                _LOGGER.warning("Speech synthesis canceled: %s", cancellation_details.reason)
                if cancellation_details.reason == CancellationReason.Error:
                    if cancellation_details.error_details:
                        _LOGGER.error("Error details: %s", cancellation_details.error_details)
        except OSError | Exception:
            _LOGGER.exception("Speech synthesis failed")
            raise

        # Save speech to memory.
        tts_stream = AudioDataStream(tts_result)

        # Save the custom macro if applicable.
        if create_custom_macro:
            tts_stream.save_to_wav_file(Const.CUSTOM_FILE)
            _LOGGER.info("Saved custom macro file: %s", Const.CUSTOM_FILE)
            return

        # Save speech to file.
        tts_stream.save_to_wav_file(Const.TTS_FILE)
        _LOGGER.info("Saved speech file: %s", Const.TTS_FILE)

        self.last_tts_text = input_text

        # Play the audio.
        self.play(Const.TTS_FILE, channel)

        # Animate the avatar.
        self.avatar_talk()

    def set_custom_macro(self) -> None:
        """Set the custom macro."""
        custom_text = self.input_text.text()
        if not custom_text:
            _LOGGER.warning("Custom Macro: No text was entered")
            return
        self.btn_cust_macro.setText(f"  {custom_text}  ")
        self.text_to_speech(custom_text, "", create_custom_macro=True)
        self.custom_macro = custom_text
        _LOGGER.info("Custom macro set: %s", custom_text)
        self.input_text.clear()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Detect when Return or Shift + Return is pressed."""
        if event.key() == Qt.Key.Key_Return:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                if not self.input_text.text():
                    _LOGGER.info("Shift + Return pressed with no text input.")
                    self.stop()
                    return
                _LOGGER.info("Shift + Return pressed for alt mic channel")
                self.text_to_speech(self.input_text.text(), Const.ALT_CHANNEL)
            else:
                if not self.input_text.text():
                    _LOGGER.warning("Return pressed with no text input.")
                    return
                _LOGGER.info("Return pressed for main mic channel")
                self.text_to_speech(self.input_text.text(), Const.MAIN_CHANNEL)

    def contextMenuEvent(self, event) -> None:
        """Detect right-clicks to show the context menu."""
        _LOGGER.info("Opening context menu")
        self.context_menu.exec(event.globalPos())

    def eventFilter(self, source, event):
        """Mouse events for dragging the window."""
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
            if QApplication.activePopupWidget() is not None:
                return True
            self.startPos = event.pos()
            return True
        elif event.type() == QEvent.MouseMove and self.startPos is not None:
            self.move(self.pos() + event.pos() - self.startPos)
            return True
        elif event.type() == QEvent.MouseButtonRelease and self.startPos is not None:
            self.startPos = None
            return True
        return super(MainWindow, self).eventFilter(source, event)


app = QApplication(sys.argv)
QFontDatabase.addApplicationFont("PTN77F.ttf")  # font from https://github.com/desero/pt-sans
app.setFont(QFont("PT Sans"))
app.setStyle("Fusion")
window = MainWindow(app)
window.show()
app.exec()
