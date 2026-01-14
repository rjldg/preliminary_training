from dotenv import load_dotenv

import azure.cognitiveservices.speech as speechsdk
import os
import time

load_dotenv()

def stop_cb(evt):
    print('CLOSING on {}'.format(evt))
    speech_recognizer.stop_continuous_recognition()
    global done
    done = True

speech_config = speechsdk.SpeechConfig(subscription=os.getenv('SPEECH_KEY'), region=os.getenv('REGION'))
speech_config.speech_recognition_language="en-US"   # Source language config

'''
speech_config.endpoint_id = "YourEndpointId"        # Custom endpoint config
speech_config.set_property(speechsdk.PropertyId.Speech_SegmentationStrategy, "Semantic")    # Enable semantic segmentation
'''

audio_config = speechsdk.audio.AudioConfig(filename='data/synthesized_audio_data/tts_output.wav')
speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

done = False

speech_recognizer.recognizing.connect(lambda evt: print('RECOGNIZING: {}'.format(evt)))
speech_recognizer.recognized.connect(lambda evt: print('RECOGNIZED: {}'.format(evt)))
speech_recognizer.session_started.connect(lambda evt: print('SESSION STARTED: {}'.format(evt)))
speech_recognizer.session_stopped.connect(lambda evt: print('SESSION STOPPED {}'.format(evt)))
speech_recognizer.canceled.connect(lambda evt: print('CANCELED {}'.format(evt)))

speech_recognizer.session_stopped.connect(stop_cb)
speech_recognizer.canceled.connect(stop_cb)

speech_recognizer.start_continuous_recognition()
while not done:
    time.sleep(.5)