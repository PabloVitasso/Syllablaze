# Set environment variables to suppress Jack errors
import os
import sys
os.environ['JACK_NO_AUDIO_RESERVATION'] = '1'
os.environ['JACK_NO_START_SERVER'] = '1'

# Completely disable Jack
os.environ['DISABLE_JACK'] = '1'

# Redirect stderr permanently to filter out Jack errors
import io
import contextlib
import threading

# Create a custom stderr filter
class JackErrorFilter:
    def __init__(self, real_stderr):
        self.real_stderr = real_stderr
        self.buffer = ""
        
    def write(self, text):
        # Filter out Jack-related error messages
        if any(msg in text for msg in [
            "jack server",
            "Cannot connect to server",
            "JackShmReadWritePtr"
        ]):
            return
        self.real_stderr.write(text)
        
    def flush(self):
        self.real_stderr.flush()

# Replace stderr with our filtered version
sys.stderr = JackErrorFilter(sys.stderr)

# Import other required modules
import pyaudio
import wave
from PyQt6.QtCore import QObject, pyqtSignal
import logging
import numpy as np
from blaze.settings import Settings
from blaze.constants import (
    WHISPER_SAMPLE_RATE, SAMPLE_RATE_MODE_WHISPER,
    SAMPLE_RATE_MODE_DEVICE, DEFAULT_SAMPLE_RATE_MODE
)
from scipy import signal
import warnings
import ctypes

logger = logging.getLogger(__name__)

class AudioRecorder(QObject):
    recording_finished = pyqtSignal(object)  # Emits audio data as numpy array
    recording_error = pyqtSignal(str)
    volume_updated = pyqtSignal(float)
    
    def __init__(self):
        super().__init__()
        
        # Create a custom error handler for audio system errors
        ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int,
                                            ctypes.c_char_p, ctypes.c_int,
                                            ctypes.c_char_p)
        
        def py_error_handler(filename, line, function, err, fmt):
            # Completely ignore all audio system errors
            pass
        
        c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
        
        # Redirect stderr to capture Jack errors
        original_stderr = sys.stderr
        sys.stderr = io.StringIO()
        
        try:
            # Try to load and configure ALSA error handler
            try:
                asound = ctypes.cdll.LoadLibrary('libasound.so.2')
                asound.snd_lib_error_set_handler(c_error_handler)
                logger.info("ALSA error handler configured")
            except:
                logger.info("ALSA error handler not available - continuing anyway")
            
            # Initialize PyAudio with all warnings suppressed
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.audio = pyaudio.PyAudio()
                
            logger.info("Audio system initialized successfully")
            
        finally:
            # Restore stderr and check if Jack errors were reported
            jack_errors = sys.stderr.getvalue()
            sys.stderr = original_stderr
            
            if "jack server is not running" in jack_errors:
                logger.info("Jack server not available - using alternative audio backend")
        
        self.stream = None
        self.frames = []
        self.is_recording = False
        self.is_testing = False
        self.test_stream = None
        self.current_device_info = None
        # Keep a reference to self to prevent premature deletion
        self._instance = self
        
    def update_sample_rate_mode(self, mode):
        """Update the sample rate mode setting"""
        settings = Settings()
        settings.set('sample_rate_mode', mode)
        logger.info(f"Sample rate mode updated to: {mode}")
        
    def start_recording(self):
        if self.is_recording:
            return
            
        try:
            self.frames = []
            self.is_recording = True
            
            # Get settings
            settings = Settings()
            mic_index = settings.get('mic_index')
            sample_rate_mode = settings.get('sample_rate_mode', DEFAULT_SAMPLE_RATE_MODE)
            
            try:
                mic_index = int(mic_index) if mic_index is not None else None
            except (ValueError, TypeError):
                mic_index = None
            
            # Get device info
            if mic_index is not None:
                device_info = self.audio.get_device_info_by_index(mic_index)
                logger.info(f"Using selected input device: {device_info['name']}")
            else:
                device_info = self.audio.get_default_input_device_info()
                logger.info(f"Using default input device: {device_info['name']}")
                mic_index = device_info['index']
            
            # Store device info for later use
            self.current_device_info = device_info
            
            # Determine sample rate based on mode
            if sample_rate_mode == SAMPLE_RATE_MODE_WHISPER:
                # Try to use 16kHz (Whisper-optimized)
                target_sample_rate = WHISPER_SAMPLE_RATE
                logger.info(f"Using Whisper-optimized sample rate: {target_sample_rate}Hz")
                
                try:
                    self.stream = self.audio.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=target_sample_rate,
                        input=True,
                        input_device_index=mic_index,
                        frames_per_buffer=1024,
                        stream_callback=self._callback
                    )
                    # If successful, store the sample rate
                    self.current_sample_rate = target_sample_rate
                    logger.info(f"Successfully recording at {target_sample_rate}Hz")
                    
                except Exception as e:
                    # If 16kHz fails, fall back to device default
                    logger.warning(f"Failed to record at {target_sample_rate}Hz: {e}")
                    logger.info("Falling back to device's default sample rate")
                    
                    default_sample_rate = int(device_info['defaultSampleRate'])
                    logger.info(f"Using fallback sample rate: {default_sample_rate}Hz")
                    
                    self.stream = self.audio.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=default_sample_rate,
                        input=True,
                        input_device_index=mic_index,
                        frames_per_buffer=1024,
                        stream_callback=self._callback
                    )
                    # Store the sample rate
                    self.current_sample_rate = default_sample_rate
                    
            else:  # SAMPLE_RATE_MODE_DEVICE
                # Use device's default sample rate
                default_sample_rate = int(device_info['defaultSampleRate'])
                logger.info(f"Using device's default sample rate: {default_sample_rate}Hz")
                
                self.stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=default_sample_rate,
                    input=True,
                    input_device_index=mic_index,
                    frames_per_buffer=1024,
                    stream_callback=self._callback
                )
                # Store the sample rate
                self.current_sample_rate = default_sample_rate
            
            self.stream.start_stream()
            logger.info(f"Recording started at {self.current_sample_rate}Hz")
            
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self.recording_error.emit(f"Failed to start recording: {e}")
            self.is_recording = False
        
    def _callback(self, in_data, frame_count, time_info, status):
        if status:
            logger.warning(f"Recording status: {status}")
        try:
            if self.is_recording:
                self.frames.append(in_data)
                # Calculate and emit volume level
                try:
                    audio_data = np.frombuffer(in_data, dtype=np.int16)
                    if len(audio_data) > 0:
                        # Calculate RMS with protection against zero/negative values
                        squared = np.abs(audio_data)**2
                        mean_squared = np.mean(squared) if np.any(squared) else 0
                        rms = np.sqrt(mean_squared) if mean_squared > 0 else 0
                        # Normalize to 0-1 range
                        volume = min(1.0, max(0.0, rms / 32768.0))
                    else:
                        volume = 0.0
                    self.volume_updated.emit(volume)
                except Exception as e:
                    logger.warning(f"Error calculating volume: {e}")
                    self.volume_updated.emit(0.0)
                return (in_data, pyaudio.paContinue)
        except RuntimeError:
            # Handle case where object is being deleted
            logger.warning("AudioRecorder object is being cleaned up")
            return (in_data, pyaudio.paComplete)
        return (in_data, pyaudio.paComplete)
        
    def stop_recording(self):
        if not self.is_recording:
            return
            
        logger.info("Stopping recording")
        self.is_recording = False
        
        try:
            # Stop and close the stream first
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
            
            # Check if we have any recorded frames
            if not self.frames:
                logger.error("No audio data recorded")
                self.recording_error.emit("No audio was recorded")
                return
            
            # Process the recording
            self._process_recording()
            
        except Exception as e:
            logger.error(f"Error stopping recording: {e}")
            self.recording_error.emit(f"Error stopping recording: {e}")

    def _process_recording(self):
        """Process the recording and keep it in memory"""
        try:
            logger.info("Processing recording in memory...")
            # Convert frames to numpy array
            audio_data = np.frombuffer(b''.join(self.frames), dtype=np.int16)
            
            if not hasattr(self, 'current_sample_rate') or self.current_sample_rate is None:
                logger.warning("No sample rate information available, assuming device default")
                if self.current_device_info is not None:
                    original_rate = int(self.current_device_info['defaultSampleRate'])
                else:
                    # If no device info is available, we have to use a reasonable default
                    # Get the default input device's sample rate
                    original_rate = int(self.audio.get_default_input_device_info()['defaultSampleRate'])
            else:
                original_rate = self.current_sample_rate
                
            # Resample to 16000Hz if needed
            if original_rate != WHISPER_SAMPLE_RATE:
                logger.info(f"Resampling audio from {original_rate}Hz to {WHISPER_SAMPLE_RATE}Hz")
                # Calculate resampling ratio
                ratio = WHISPER_SAMPLE_RATE / original_rate
                output_length = int(len(audio_data) * ratio)
                
                # Resample audio
                audio_data = signal.resample(audio_data, output_length)
            else:
                logger.info(f"No resampling needed, audio already at {WHISPER_SAMPLE_RATE}Hz")
            
            # Normalize the audio data to float32 in the range [-1.0, 1.0] as expected by Whisper
            audio_data = audio_data.astype(np.float32) / 32768.0
            
            logger.info("Recording processed in memory")
            self.recording_finished.emit(audio_data)
        except Exception as e:
            logger.error(f"Failed to process recording: {e}")
            self.recording_error.emit(f"Failed to process recording: {e}")
        
    def save_audio(self, filename):
        """Save recorded audio to a WAV file"""
        try:
            # Convert frames to numpy array
            audio_data = np.frombuffer(b''.join(self.frames), dtype=np.int16)
            
            if not hasattr(self, 'current_sample_rate') or self.current_sample_rate is None:
                logger.warning("No sample rate information available, assuming device default")
                if self.current_device_info is not None:
                    original_rate = int(self.current_device_info['defaultSampleRate'])
                else:
                    # If no device info is available, we have to use a reasonable default
                    # Get the default input device's sample rate
                    original_rate = int(self.audio.get_default_input_device_info()['defaultSampleRate'])
            else:
                original_rate = self.current_sample_rate
                
            # Resample to 16000Hz if needed
            if original_rate != WHISPER_SAMPLE_RATE:
                logger.info(f"Resampling audio from {original_rate}Hz to {WHISPER_SAMPLE_RATE}Hz")
                # Calculate resampling ratio
                ratio = WHISPER_SAMPLE_RATE / original_rate
                output_length = int(len(audio_data) * ratio)
                
                # Resample audio
                audio_data = signal.resample(audio_data, output_length)
            else:
                logger.info(f"No resampling needed, audio already at {WHISPER_SAMPLE_RATE}Hz")
            
            # Save to WAV file
            wf = wave.open(filename, 'wb')
            wf.setnchannels(1)
            wf.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
            wf.setframerate(WHISPER_SAMPLE_RATE)  # Always save at 16000Hz for Whisper
            wf.writeframes(audio_data.astype(np.int16).tobytes())
            wf.close()
            
            # Log the saved file location
            logger.info(f"Recording saved to: {os.path.abspath(filename)}")
            
        except Exception as e:
            logger.error(f"Failed to save audio file: {e}")
            raise
        
    def start_mic_test(self, device_index):
        """Start microphone test"""
        if self.is_testing or self.is_recording:
            return
            
        try:
            self.test_stream = self.audio.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=44100,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=1024,
                stream_callback=self._test_callback
            )
            
            self.test_stream.start_stream()
            self.is_testing = True
            logger.info(f"Started mic test on device {device_index}")
            
        except Exception as e:
            logger.error(f"Failed to start mic test: {e}")
            raise
            
    def stop_mic_test(self):
        """Stop microphone test"""
        if self.test_stream:
            self.test_stream.stop_stream()
            self.test_stream.close()
            self.test_stream = None
        self.is_testing = False
        
    def _test_callback(self, in_data, frame_count, time_info, status):
        """Callback for mic test"""
        if status:
            logger.warning(f"Test callback status: {status}")
        return (in_data, pyaudio.paContinue)
        
    def get_current_audio_level(self):
        """Get current audio level for meter"""
        if not self.test_stream or not self.is_testing:
            return 0
            
        try:
            data = self.test_stream.read(1024, exception_on_overflow=False)
            audio_data = np.frombuffer(data, dtype=np.float32)
            return np.sqrt(np.mean(np.square(audio_data)))
        except Exception as e:
            logger.error(f"Error getting audio level: {e}")
            return 0

    def cleanup(self):
        """Cleanup resources"""
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        if self.test_stream:
            self.test_stream.stop_stream()
            self.test_stream.close()
            self.test_stream = None
        if self.audio:
            self.audio.terminate()
            self.audio = None
        self._instance = None 