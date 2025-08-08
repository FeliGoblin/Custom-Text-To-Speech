import re
import sys
import aiofiles
import vlc
import logging
import asyncio
from functools import partial
from enum import StrEnum
from qasync import QEventLoop, asyncSlot

from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QIcon,
    QKeyEvent,
    QMouseEvent,
)
from PySide6.QtCore import QSize, Qt, QTimer, QEvent, QPointF, QMetaObject, Slot

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

from const import (
    Const,
    OBS,
    Emotion,
    Voice,
    RaidIcon,
    WebSocketIcon,
    PhraseMacro,
    FIXES,
    PRONUNCIATIONS,
    ON,
    OFF,
)
import simpleobsws

logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger("TTS")


class MainWindow(QMainWindow):
    """Application window."""

    def __init__(self, app):
        super().__init__()
        self.app: QApplication = app
        self.setWindowTitle("Text to Speech")
        QApplication.instance().installEventFilter(self)
        self.start_pos: QPointF = None
        self.dragging: bool = False

        # VLC
        self.player: vlc.MediaPlayer = vlc.MediaPlayer()
        self.custom_macro_text: str = ""

        # Azure
        self.speech_synthesizer: SpeechSynthesizer | None = None
        self.tts_emotion: str = Emotion.FRIENDLY
        self.tts_voice: str = Voice.EN_JANE
        self.setup_synthesis()

        # OBS
        self.websocket: simpleobsws.WebSocketClient | None = None
        self.websocket_reconnect_task: asyncio.Task | None = None
        self.websocket_connected: bool = False
        self.avatar_item_id: int | None = None
        self.bubble_item_id: int | None = None
        self.html_template: str = ""
        self.last_tts_text: str = ""

        # Widgets
        self.btn_cust_macro: QPushButton | None = None
        self.input_text: QLineEdit | None = None
        self.menu_emotion: QMenu | None = None
        self.menu_voice: QMenu | None = None

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        row_1, row_2, row_3 = QHBoxLayout(), QHBoxLayout(), QHBoxLayout()

        def make_button(
            text: str = "",
            icon: str | QIcon | None = None,
            size: tuple | None = None,
            tooltip=None,
            click=None,
            min_size: tuple | None = None,
        ):
            btn = QPushButton(text)
            if icon:
                btn.setIcon(QIcon(icon) if isinstance(icon, str) else icon)
            if size:
                btn.setIconSize(QSize(*size))
                btn.setFixedSize(*size)
            if tooltip:
                btn.setToolTip(tooltip)
            if min_size:
                btn.setMinimumSize(*min_size)
            if click:
                btn.clicked.connect(click)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            return btn

        def populate_menu(menu: QMenu, items: type[StrEnum], handler):
            """Fill a QMenu with QAction items from an iterable, binding each to handler."""
            for value in items:
                menu.addAction(value.value).triggered.connect(
                    lambda _, v=value.value: handler(v)
                )

        # Raid Icon Macros
        for icon in RaidIcon:
            row_1.addWidget(
                make_button(
                    icon=Const.ICON_FILE.replace(Const.REPLACE, icon.value, 1),
                    size=(30, 30),
                    click=partial(self.play_macro, icon.value),
                )
            )

        # OBS Connection Button
        self.btn_websocket = make_button(
            icon=WebSocketIcon.OFF, size=(25, 30), click=self.trigger_websocket
        )
        self.btn_websocket.setIconSize(QSize(15, 15))
        self.btn_websocket.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        row_1.addWidget(self.btn_websocket)

        # Spacer
        row_1.addWidget(QLabel(""))

        # Custom Macro
        self.btn_cust_macro = make_button(
            "  Custom Macro  ", click=lambda: self.play_macro(Const.CUSTOM)
        )
        row_1.addWidget(self.btn_cust_macro)

        # Phrase Macros
        for phrase in PhraseMacro:
            row_2.addWidget(
                make_button(
                    phrase.value.get(Const.LABEL, ""),
                    size=(phrase.value.get(Const.WIDTH, 40), 30),
                    click=partial(self.play_macro, phrase.name.lower()),
                )
            )

        # Text Input
        self.input_text = QLineEdit()
        self.input_text.setMinimumSize(340, 30)
        self.input_text.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.input_text.setMouseTracking(True)
        row_3.addWidget(self.input_text)

        # Stop & Repeat Buttons
        row_3.addWidget(make_button("Stop", size=(50, 30), click=self.stop))
        row_3.addWidget(
            make_button("Repeat", size=(60, 30), click=lambda: self.play_macro(Const.REPEAT))
        )

        # Layout Stuff
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
        self.context_menu.addAction("Set Custom Macro").triggered.connect(self.set_custom_macro)
        self.context_menu.addAction("Exit").triggered.connect(
            lambda: asyncio.create_task(self.shutdown())
        )

        # Fill menus
        populate_menu(self.menu_emotion, Emotion, self.set_emotion)
        populate_menu(self.menu_voice, Voice, self.set_voice)

    async def setup(self):
        self.websocket_reconnect_task = asyncio.create_task(self.connect_obs_websocket())

    # ------------------------------
    # VLC Media Player
    # ------------------------------

    def play(self, file: str, channel: str) -> None:
        """Play audio file."""
        self.player.stop()
        self.player.set_media(vlc.Media(file))
        self.player.audio_output_device_set(None, channel)
        _LOGGER.info("Playing audio file (%s) on %s", file, Const(channel).name)
        self.player.play()

    def stop(self) -> None:
        """Stop the player."""
        _LOGGER.info("Stopping the player")
        self.player.stop()

    @asyncSlot()
    async def play_macro(self, macro: str) -> None:
        """Play the macro file."""
        if macro == Const.REPEAT:
            file = Const.TTS_FILE
            text = ""
        elif macro == Const.CUSTOM:
            if not self.custom_macro_text:
                if not self.input_text.text():
                    _LOGGER.warning("A custom macro has not been set")
                    await self.set_progress_message("A custom macro has not been set", 4)
                    return
                return self.set_custom_macro()
            file = Const.CUSTOM_FILE
            text = self.custom_macro_text
        else:
            file = Const.MACRO_FILE.replace(Const.REPLACE, macro, 1)
            try:
                text = RaidIcon(macro).value + (
                    Const.REPLACE if macro != RaidIcon.UNMARKED else ""
                )
                _LOGGER.info("Macro is a raid icon")
            except ValueError:
                text = PhraseMacro[macro.upper()].value[Const.PHRASE]
                _LOGGER.info("Macro is a phrase")

        _LOGGER.info(f"Playing macro: {macro}")
        self.play(file, Const.MAIN_CHANNEL)
        await self.avatar_talk(text)

    # ------------------------------
    # Settings Menus
    # ------------------------------

    @asyncSlot()
    async def set_emotion(self, emotion: str) -> None:
        """Set the emotion for the TTS."""
        _LOGGER.info("Setting emotion to: %s", emotion)
        self.tts_emotion = emotion
        await self.set_progress_message()

    @asyncSlot()
    async def set_voice(self, voice: str) -> None:
        """Set the voice for the TTS."""
        _LOGGER.info("Setting voice to: %s", voice)
        self.tts_voice = voice
        await self.set_progress_message()

    @asyncSlot()
    async def set_custom_macro(self) -> None:
        """Set the custom macro."""
        custom_text = self.input_text.text()
        if not custom_text:
            _LOGGER.warning("Custom Macro: No text was entered")
            await self.set_progress_message("Type macro text here first!", 4)
            return
        self.btn_cust_macro.setText(f"  {custom_text}  ")
        await self.text_to_speech(custom_text, "", create_custom_macro=True)
        self.custom_macro_text = custom_text
        self.input_text.clear()
        _LOGGER.info("Custom macro set: %s", custom_text)
        await self.set_progress_message(f"Custom macro set to: {custom_text}", 4)

    # ------------------------------
    # LineEdit Information Text
    # ------------------------------

    async def set_progress_message(self, text: str = "", show_for: float = 0) -> None:
        """Put a non-interactive message in the text input area to show status and progress messages.

        With no args given, Voice information is shown as default.
        Optionally show message for set amount of seconds before returning to default."""
        if not text:
            voice = self.tts_voice.rsplit("-", 1)[-1]
            if "Neural" in voice:
                voice = voice.removesuffix("Neural")
            text = f" {voice} - {self.tts_emotion}"

        _LOGGER.info("Setting the line-edit placeholder to: %s", text)
        self.input_text.setPlaceholderText(text)
        if show_for:
            await asyncio.sleep(show_for)
            await self.set_progress_message()

    # ------------------------------
    # Azure Text to Speech
    # ------------------------------

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

    @Slot()
    def clear_text_input(self):
        self.input_text.clear()

    async def text_to_speech(
        self, input_text: str, channel: str, *, create_custom_macro: bool = False
    ) -> None:
        """Synthesize speech and play it on selected channel.

        Optionally create the custom macro without playing it.
        """

        # text_to_speech() not in GUI thread, so interact with widget with invokeMethod
        QMetaObject.invokeMethod(self, "clear_text_input")
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
            '<speak xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="http://www.w3.org/2001/mstts" '
            + 'xmlns:emo="http://www.w3.org/2009/10/emotionml" version="1.0" xml:lang="en-US">'
            + f'<voice name="{self.tts_voice}"><mstts:express-as style="{self.tts_emotion.lower()}" styledegree="1">'
            + f'<prosody rate="{tts_rate}%" pitch="{tts_pitch}%">{tts_input}</prosody>'
            + "</mstts:express-as></voice></speak>"
        )

        tts_result = await asyncio.to_thread(
            self.speech_synthesizer.speak_ssml_async(tts_ssml).get
        )
        if tts_result.reason == ResultReason.Canceled:
            cancellation_details = tts_result.cancellation_details
            _LOGGER.warning("Speech synthesis canceled: %s", cancellation_details.reason)
            if cancellation_details.reason == CancellationReason.Error:
                if cancellation_details.error_details:
                    _LOGGER.error("Error details: %s", cancellation_details.error_details)
            return

        # Save speech to memory.
        tts_stream = AudioDataStream(tts_result)

        # Save the custom macro if applicable.
        if create_custom_macro:
            tts_stream.save_to_wav_file(Const.CUSTOM_FILE)
            _LOGGER.info("Saved custom macro file: %s", Const.CUSTOM_FILE)
            return

        tts_stream.save_to_wav_file(Const.TTS_FILE)
        _LOGGER.info("Saved speech file: %s", Const.TTS_FILE)
        self.last_tts_text = input_text
        self.play(Const.TTS_FILE, channel)
        await self.avatar_talk()

    # ------------------------------
    # OBS Websocket
    # ------------------------------

    @asyncSlot()
    async def trigger_websocket(self):
        # if self.websocket and self.websocket.is_identified():
        if self.websocket:
            await self.config_websocket_status(OFF)
            await self.set_progress_message("OBS Disconnected", 2)
            return
        self.websocket_reconnect_task = asyncio.create_task(self.connect_obs_websocket())

    async def config_websocket_status(self, toggle: bool, soft: bool = False) -> None:
        if not toggle:
            if not soft:
                # if self.websocket and self.websocket.is_identified():
                if self.websocket:
                    await self.websocket.disconnect()
                if self.websocket_reconnect_task:
                    self.websocket_reconnect_task.cancel()
            self.websocket = None
            self.websocket_reconnect_task = None

        self.websocket_connected = toggle
        self.btn_websocket.setIcon(WebSocketIcon.ON if toggle else WebSocketIcon.OFF)

    async def connect_obs_websocket(self) -> None:
        """Set up async OBS WebSocket client and get scene item IDs."""
        delay = 20  # seconds between connection checks
        retries = 3  # number of retries after a disconnect
        attempt = 0
        disconnected = False

        self.websocket = simpleobsws.WebSocketClient(
            url=f"ws://{OBS.HOST}:{OBS.PORT}", password=OBS.PWD
        )

        while True:
            if self.websocket_connected:
                if self.websocket and self.websocket.is_identified():
                    _LOGGER.info("OBS WebSocket identified.")
                    await asyncio.sleep(delay)
                    continue
                _LOGGER.warning("OBS WebSocket disconnected, attempting reconnect...")
                self.websocket_connected = False
                disconnected = True
                self.btn_websocket.setIcon(WebSocketIcon.OFF_RED)

            if attempt >= retries:
                _LOGGER.warning("Connection failed. Stopping attempts.")
                await self.set_progress_message()
                await self.config_websocket_status(OFF)
                break
            attempt += 1

            _LOGGER.info("Attempting to connect to OBS WebSocket...")
            if disconnected:
                dc = "OBS disconnected! " if attempt == 1 else ""
                await self.set_progress_message(dc + "Attempting to reconnect...")
            else:
                await self.set_progress_message("Connecting to OBS WebSocket...")

            try:
                await self.websocket.connect()
                await self.websocket.wait_until_identified()

                # Get avatar and speech-bubble item IDs
                avatar_response = await self.websocket.call(
                    simpleobsws.Request(
                        "GetSceneItemId",
                        {"sceneName": OBS.AVA_SCENE, "sourceName": OBS.AVA_SOURCE},
                    )
                )
                self.avatar_item_id = avatar_response.responseData["sceneItemId"]

                bubble_response = await self.websocket.call(
                    simpleobsws.Request(
                        "GetSceneItemId",
                        {"sceneName": OBS.BUB_SCENE, "sourceName": OBS.BUB_SOURCE},
                    )
                )
                self.bubble_item_id = bubble_response.responseData["sceneItemId"]

                _LOGGER.info("Connected to OBS!")
                attempt = 0
                disconnected = False
                await self.config_websocket_status(ON)
                await self.set_progress_message("Connected to OBS!", 2)

            except Exception as e:
                _LOGGER.warning(f"OBS connection failed: {e}")
                self.btn_websocket.setIcon(WebSocketIcon.OFF_RED)
                await self.set_progress_message(str(e).split("]", 1)[-1], 5)
                if not disconnected:
                    await self.config_websocket_status(OFF)
                    break
            else:
                if not self.html_template:
                    await self.load_html_template()
                await asyncio.sleep(delay)

    async def load_html_template(self):
        async with aiofiles.open("speech-bubble-template.html", "r") as f:
            self.html_template = await f.read()

    async def avatar_talk(self, override: str | None = None) -> None:
        """Make the on-screen avatar talk while the speech audio is playing."""
        if self.websocket and self.websocket.is_identified():
            await asyncio.sleep(0.2)
            await self.send_speech_bubble_text(True, override or self.last_tts_text)
            await self.move_mouth(True)
            while self.player.is_playing():
                await self.move_mouth(False)
                await asyncio.sleep(0.2)
                await self.move_mouth(True)
                await asyncio.sleep(0.2)
            await self.move_mouth(False)
            await self.send_speech_bubble_text(False)

    async def move_mouth(self, enable: bool) -> None:
        """Enable and disable the open-mouth image of the avatar."""
        if self.avatar_item_id:
            await self.websocket.call(
                simpleobsws.Request(
                    "SetSceneItemEnabled",
                    {
                        "sceneName": OBS.AVA_SCENE,
                        "sceneItemId": self.avatar_item_id,
                        "sceneItemEnabled": enable,
                    },
                )
            )

    async def send_speech_bubble_text(self, enable: bool, text: str = "") -> None:
        """Update the speech-bubble HTML, and enable/disable the speech-bubble browser source."""
        if not self.html_template:
            await self.load_html_template()
        if not self.html_template:
            await self.set_progress_message("HTML template could not be loaded")

        if text and self.html_template:
            icon = ""
            if text.endswith(Const.REPLACE):
                text = text.removesuffix(Const.REPLACE)
                icon = f'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<img src="icons/{text}.png" />'
            html = self.html_template.replace(Const.REPLACE, text.capitalize() + icon, 1)
            async with aiofiles.open("speech-bubble.html", "w") as f:
                await f.write(html)

        if self.bubble_item_id:
            await self.websocket.call(
                simpleobsws.Request(
                    "SetSceneItemEnabled",
                    {
                        "sceneName": OBS.BUB_SCENE,
                        "sceneItemId": self.bubble_item_id,
                        "sceneItemEnabled": enable,
                    },
                )
            )
        else:
            await self.set_progress_message("Speech bubble ID not loaded")

    # ------------------------------
    # Event managers
    # ------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Detect when Return or Shift + Return is pressed."""
        if event.key() == Qt.Key.Key_Return:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                if self.input_text.text():
                    _LOGGER.info("Shift + Return pressed for alt mic channel")
                    asyncio.create_task(
                        self.text_to_speech(self.input_text.text(), Const.ALT_CHANNEL)
                    )
                else:
                    _LOGGER.info("Shift + Return pressed with no text input. Stopping player.")
                    self.stop()
            elif self.input_text.text():
                _LOGGER.info("Return pressed for main mic channel")
                asyncio.create_task(
                    self.text_to_speech(self.input_text.text(), Const.MAIN_CHANNEL)
                )
            else:
                _LOGGER.info("Return pressed with no text input.")

    def contextMenuEvent(self, event: QMouseEvent) -> None:
        """Detect right-clicks to show the context menu."""
        _LOGGER.info("Opening context menu")
        self.context_menu.exec(event.globalPos())

    def closeEvent(self, event):
        """Override window close to ensure async shutdown is triggered."""
        asyncio.create_task(self.shutdown())  # fire off coroutine safely
        super().closeEvent(event)

    def eventFilter(self, source, event):
        if isinstance(event, QMouseEvent):
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and not self.input_text.underMouse()
                and event.button()
                in (
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.MiddleButton,
                )
            ):
                self.start_pos = event.globalPosition() - self.frameGeometry().topLeft()
                self.dragging = False
            elif event.type() == QEvent.Type.MouseMove and self.start_pos is not None:
                if not self.dragging:
                    if (event.globalPosition() - self.start_pos).manhattanLength() > 5:
                        self.dragging = True
                if self.dragging:
                    new_pos = event.globalPosition() - self.start_pos
                    self.move(new_pos.toPoint())
                    return True  # Block event to prevent unintended widget actions
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self.start_pos = None
                if self.dragging:
                    self.dragging = False
                    return True  # Block event to prevent unintended widget actions
        return super().eventFilter(source, event)

    # ------------------------------
    # Shutting down
    # ------------------------------

    def exit_app(self):
        asyncio.create_task(self.shutdown())

    async def shutdown(self):
        """Disconnect from OBS and clean up."""
        _LOGGER.info("Shutting down...")
        try:
            await self.config_websocket_status(OFF)
            _LOGGER.info("OBS WebSocket disconnected.")
        except Exception as e:
            _LOGGER.warning(f"Error while disconnecting OBS WebSocket: {e}")
        self.stop()
        QTimer.singleShot(0, self.app.quit)


def setup_event_loop(app):
    """Starting the event loop."""
    # from PySide6.QtAsyncio import QAsyncioEventLoop
    # loop = QAsyncioEventLoop(app)
    loop = QEventLoop(app)  # Remove this line and uncomment above if switching to QtAsyncio
    # asyncSlot() may need changing as well..
    asyncio.set_event_loop(loop)
    return loop


async def main():
    app = QApplication(sys.argv)
    QFontDatabase.addApplicationFont(
        "PTN77F.ttf"  # font from https://github.com/desero/pt-sans
    )
    app.setFont(QFont("PT Sans"))
    app.setStyle("Fusion")

    loop = setup_event_loop(app)

    window = MainWindow(app)
    window.show()

    with loop:
        _LOGGER.info("Starting event loop and scheduling OBS WebSocket setup...")
        loop.call_soon(asyncio.create_task, window.setup())
        try:
            loop.run_forever()
        finally:
            _LOGGER.info("Loop stopped. Cleaning up...")
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()


if __name__ == "__main__":
    asyncio.run(main())
