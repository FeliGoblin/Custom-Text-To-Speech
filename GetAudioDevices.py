import vlc
import time

p = vlc.MediaPlayer("output.wav")

p.play()

device = p.audio_output_device_enum()
while device:
    print("playing on...")
    print(device.contents.device)
    print(device.contents.description)

    p.audio_output_device_set(None, device.contents.device)
    time.sleep(3)

    device = device.contents.next

p.stop()