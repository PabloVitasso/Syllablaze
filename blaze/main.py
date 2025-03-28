import os
import sys
import signal
from PyQt6.QtWidgets import (QApplication, QMessageBox, QSystemTrayIcon, QMenu)
from PyQt6.QtCore import Qt, QTimer, QCoreApplication
from PyQt6.QtGui import QIcon, QAction
import logging
from blaze.settings_window import SettingsWindow
from blaze.progress_window import ProgressWindow
from blaze.processing_window import ProcessingWindow
from blaze.recorder import AudioRecorder
from blaze.transcriber import WhisperTranscriber
from blaze.loading_window import LoadingWindow
from PyQt6.QtCore import pyqtSignal
import warnings
import ctypes
from blaze.settings import Settings
from blaze.constants import APP_NAME, APP_VERSION, DEFAULT_WHISPER_MODEL, ORG_NAME, VALID_LANGUAGES
from blaze.whisper_model_manager import get_model_info
# from blaze.mic_debug import MicDebugWindow

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Audio error handling is now done in recorder.py
# This comment is kept for documentation purposes

def check_dependencies():
    required_packages = ['whisper', 'pyaudio', 'keyboard']
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(package)
            logger.error(f"Failed to import required dependency: {package}")
    
    if missing_packages:
        error_msg = (
            "Missing required dependencies:\n"
            f"{', '.join(missing_packages)}\n\n"
            "Please install them using:\n"
            f"pip install {' '.join(missing_packages)}"
        )
        QMessageBox.critical(None, "Missing Dependencies", error_msg)
        return False
        
    return True

# Global variable to store the tray recorder instance
tray_recorder_instance = None

def get_tray_recorder():
    """Get the global tray recorder instance"""
    return tray_recorder_instance

def update_tray_tooltip():
    """Update the tray tooltip"""
    if tray_recorder_instance:
        tray_recorder_instance.update_tooltip()

class TrayRecorder(QSystemTrayIcon):
    initialization_complete = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        
        # Store the instance in the global variable
        global tray_recorder_instance
        tray_recorder_instance = self
        
        # Initialize basic state
        self.recording = False
        self.settings_window = None
        self.progress_window = None
        self.processing_window = None
        self.recorder = None
        self.transcriber = None
        
        # Create debug window but don't show it
        # self.debug_window = MicDebugWindow()
        
        # Set tooltip
        self.setToolTip(f"{APP_NAME} {APP_VERSION}")
        
        # Enable activation by left click
        self.activated.connect(self.on_activate)

    def initialize(self):
        """Initialize the tray recorder after showing loading window"""
        # Set application icon
        self.app_icon = QIcon.fromTheme("syllablaze")
        if self.app_icon.isNull():
            # Try to load from local path
            local_icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "syllablaze.png")
            if os.path.exists(local_icon_path):
                self.app_icon = QIcon(local_icon_path)
            else:
                # Fallback to theme icons if custom icon not found
                self.app_icon = QIcon.fromTheme("media-record")
                logger.warning("Could not load syllablaze icon, using system theme icon")
            
        # Set the icon for both app and tray
        QApplication.instance().setWindowIcon(self.app_icon)
        self.setIcon(self.app_icon)
        
        # Use app icon for normal state and theme icon for recording
        self.normal_icon = self.app_icon
        self.recording_icon = QIcon.fromTheme("media-playback-stop")
        
        # Create menu
        self.setup_menu()
            
        # Initialize tooltip with model information
        self.update_tooltip()
            
    def setup_menu(self):
        menu = QMenu()
        
        # Add recording action
        self.record_action = QAction("Start Recording", menu)
        self.record_action.triggered.connect(self.toggle_recording)
        menu.addAction(self.record_action)
        
        # Add settings action
        self.settings_action = QAction("Settings", menu)
        self.settings_action.triggered.connect(self.toggle_settings)
        menu.addAction(self.settings_action)

        # Add separator before quit
        menu.addSeparator()
        
        # Add quit action
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit_application)
        menu.addAction(quit_action)
        
        # Set the context menu
        self.setContextMenu(menu)

    @staticmethod
    def isSystemTrayAvailable():
        return QSystemTrayIcon.isSystemTrayAvailable()

    def toggle_recording(self):
        if self.recording:
            # Stop recording
            self.recording = False
            self.record_action.setText("Start Recording")
            self.setIcon(self.normal_icon)
            
            # Update progress window before stopping recording
            if self.progress_window:
                self.progress_window.set_processing_mode()
                self.progress_window.set_status("Processing audio...")
            
            # Stop the actual recording
            if self.recorder:
                try:
                    self.recorder.stop_recording()
                except Exception as e:
                    logger.error(f"Error stopping recording: {e}")
                    if self.progress_window:
                        self.progress_window.close()
                        self.progress_window = None
                    return
        else:
            # Start recording
            self.recording = True
            # Show progress window
            if not self.progress_window:
                self.progress_window = ProgressWindow("Voice Recording")
                self.progress_window.stop_clicked.connect(self.stop_recording)
            self.progress_window.show()
            
            # Start recording
            self.record_action.setText("Stop Recording")
            self.setIcon(self.recording_icon)
            self.recorder.start_recording()

    def stop_recording(self):
        """Handle stopping the recording and starting processing"""
        if not self.recording:
            return
        
        logger.info("TrayRecorder: Stopping recording")
        self.toggle_recording()  # This is now safe since toggle_recording handles everything

    def toggle_settings(self):
        if not self.settings_window:
            self.settings_window = SettingsWindow()
        
        if self.settings_window.isVisible():
            self.settings_window.hide()
        else:
            # Show the window (not maximized)
            self.settings_window.show()
            # Bring to front and activate
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            
    def update_tooltip(self, recognized_text=None):
        """Update the tooltip with app name, version, model and language information"""
        import sys
        
        settings = Settings()
        model_name = settings.get('model', DEFAULT_WHISPER_MODEL)
        language_code = settings.get('language', 'auto')
        
        # Get language display name from VALID_LANGUAGES if available
        if language_code in VALID_LANGUAGES:
            language_display = f"Language: {VALID_LANGUAGES[language_code]}"
        else:
            language_display = "Language: auto-detect" if language_code == 'auto' else f"Language: {language_code}"
        
        tooltip = f"{APP_NAME} {APP_VERSION}\nModel: {model_name}\n{language_display}"
        
        # Add recognized text to tooltip if provided
        if recognized_text:
            # Truncate text if it's too long
            max_length = 100
            if len(recognized_text) > max_length:
                recognized_text = recognized_text[:max_length] + "..."
            tooltip += f"\nRecognized: {recognized_text}"
        
        # Print tooltip info to console with flush
        print(f"TOOLTIP UPDATE: MODEL={model_name}, {language_display}", flush=True)
        sys.stdout.flush()
            
        self.setToolTip(tooltip)
    
    # Removed update_shortcuts method as part of keyboard shortcut functionality removal

    def on_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Left click
            self.toggle_recording()

    def quit_application(self):
        import os
        try:
            # Cleanup recorder
            if self.recorder:
                try:
                    self.recorder.cleanup()
                except Exception as rec_error:
                    logger.error(f"Error cleaning up recorder: {rec_error}")
                self.recorder = None
            
            # Close all windows
            if hasattr(self, 'settings_window') and self.settings_window:
                try:
                    if self.settings_window.isVisible():
                        self.settings_window.close()
                except Exception as win_error:
                    logger.error(f"Error closing settings window: {win_error}")
                
            if hasattr(self, 'progress_window') and self.progress_window:
                try:
                    if self.progress_window.isVisible():
                        self.progress_window.close()
                except Exception as win_error:
                    logger.error(f"Error closing progress window: {win_error}")
                
            # Stop recording if active
            if self.recording:
                try:
                    self.stop_recording()
                except Exception as rec_error:
                    logger.error(f"Error stopping recording: {rec_error}")
            
            # Wait for any running threads to finish
            if hasattr(self, 'transcriber') and self.transcriber:
                try:
                    if hasattr(self.transcriber, 'worker') and self.transcriber.worker:
                        if self.transcriber.worker.isRunning():
                            logger.info("Waiting for transcription worker to finish...")
                            self.transcriber.worker.wait(5000)  # Wait up to 5 seconds
                except Exception as thread_error:
                    logger.error(f"Error waiting for transcription worker: {thread_error}")
            
            # Release lock file if it exists
            global LOCK_FILE
            if LOCK_FILE:
                try:
                    import fcntl
                    # Release the lock
                    fcntl.flock(LOCK_FILE, fcntl.LOCK_UN)
                    LOCK_FILE.close()
                    # Remove the lock file
                    if os.path.exists(LOCK_FILE_PATH):
                        os.remove(LOCK_FILE_PATH)
                    LOCK_FILE = None
                    logger.info("Released application lock file")
                except Exception as lock_error:
                    logger.error(f"Error releasing lock file: {lock_error}")
            
            logger.info("Application shutdown complete, exiting...")
            
            # Explicitly quit the application
            QApplication.instance().quit()
                
        except Exception as e:
            logger.error(f"Error during application shutdown: {e}")
            # Force exit if there was an error
            # Remove forced exit on error

    def update_volume_meter(self, value):
        # Update debug window first
        if hasattr(self, 'debug_window'):
            self.debug_window.update_values(value)
            
        # Then update volume meter as before
        if self.progress_window and self.recording:
            self.progress_window.update_volume(value)
    
    def handle_recording_finished(self, audio_data):
        """Called when recording is processed in memory"""
        logger.info("TrayRecorder: Recording processed, starting transcription")
        
        # Ensure progress window is in processing mode
        if self.progress_window:
            self.progress_window.set_processing_mode()
            self.progress_window.set_status("Starting transcription...")
        
        if self.transcriber:
            self.transcriber.transcribe_file(audio_data)
        else:
            logger.error("Transcriber not initialized")
            if self.progress_window:
                self.progress_window.close()
                self.progress_window = None
            
            # Show notification instead of dialog
            self.showMessage("Error",
                           "Transcriber not initialized",
                           self.normal_icon)
    
    def handle_recording_error(self, error):
        """Handle recording errors"""
        logger.error(f"TrayRecorder: Recording error: {error}")
        
        # Show notification instead of dialog
        self.showMessage("Recording Error",
                       error,
                       self.normal_icon)
        
        self.stop_recording()
        if self.progress_window:
            self.progress_window.close()
            self.progress_window = None
    
    def update_processing_status(self, status):
        if self.progress_window:
            self.progress_window.set_status(status)
            
    def update_processing_progress(self, percent):
        if self.progress_window:
            self.progress_window.update_progress(percent)
    
    def handle_transcription_finished(self, text):
        if text:
            # Copy text to clipboard
            QApplication.clipboard().setText(text)
            
            # Truncate text for notification if it's too long
            display_text = text
            if len(text) > 100:
                display_text = text[:100] + "..."
                
            # Show notification with the transcribed text
            self.showMessage("Transcription Complete",
                           f"{display_text}",
                           self.normal_icon)
            
            # Update tooltip with recognized text
            self.update_tooltip(text)
        
        # Close the progress window
        if self.progress_window:
            self.progress_window.close()
            self.progress_window = None
    
    def handle_transcription_error(self, error):
        self.showMessage("Transcription Error",
                       error,
                       self.normal_icon)
        
        # Update tooltip to indicate error
        self.update_tooltip()
        
        if self.progress_window:
            self.progress_window.close()
            self.progress_window = None

    def stop_recording(self):
        """Handle stopping the recording and starting processing"""
        if not self.recording:
            return
        
        logger.info("TrayRecorder: Stopping recording")
        self.toggle_recording()  # This is now safe since toggle_recording handles everything

    def toggle_debug_window(self):
        """Toggle debug window visibility"""
        if self.debug_window.isVisible():
            self.debug_window.hide()
            self.debug_action.setText("Show Debug Window")
        else:
            self.debug_window.show()
            self.debug_action.setText("Hide Debug Window")

def setup_application_metadata():
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(APP_VERSION)
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setOrganizationDomain("kde.org")

# Global variable for lock file
LOCK_FILE_PATH = os.path.expanduser("~/.cache/syllablaze/syllablaze.lock")
LOCK_FILE = None


















def check_already_running():
    """Check if Syllablaze is already running using a file lock mechanism"""
    global LOCK_FILE
    
    


    # Create directory if it doesn't exist
    lock_dir = os.path.dirname(LOCK_FILE_PATH)
    if not os.path.exists(lock_dir):
        try:
            os.makedirs(lock_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create lock directory: {e}")
            # Fall back to process-based check if we can't create the lock directory
            return _check_already_running_by_process()
    
    try:
        # Try to create and lock the file
        import fcntl
        
        # Check if the lock file exists
        if os.path.exists(LOCK_FILE_PATH):
            try:
                # Try to open the existing lock file for reading and writing
                test_lock = open(LOCK_FILE_PATH, 'r+')
                try:
                    # Try to get a non-blocking exclusive lock
                    fcntl.flock(test_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    
                    # If we got here, the file wasn't locked
                    # Read the PID from the file
                    test_lock.seek(0)
                    pid = test_lock.read().strip()
                    
                    # Check if the process with this PID is still running
                    if pid and pid.isdigit():
                        try:
                            # If we can send signal 0 to the process, it exists
                            os.kill(int(pid), 0)
                            # This is strange - the file exists and the process exists,
                            # but the file wasn't locked. This could happen if the process
                            # crashed without cleaning up. Let's assume it's not running.
                            logger.warning(f"Found process {pid} but lock file wasn't locked. Assuming stale lock.")
                        except OSError:
                            # Process doesn't exist
                            logger.info(f"Removing stale lock file for PID {pid}")
                    
                    # Release the lock and close the file
                    fcntl.flock(test_lock, fcntl.LOCK_UN)
                    test_lock.close()
                    
                    # Remove the stale lock file
                    os.remove(LOCK_FILE_PATH)
                except IOError:
                    # The file is locked by another process
                    test_lock.close()
                    logger.info("Lock file is locked by another process")
                    return True
            except Exception as e:
                logger.error(f"Error checking existing lock file: {e}")
                # If we can't read the lock file, try to remove it
                try:
                    os.remove(LOCK_FILE_PATH)
                except:
                    pass
        
        # Create a new lock file
        LOCK_FILE = open(LOCK_FILE_PATH, 'w')
        # Write PID to the file
        LOCK_FILE.write(str(os.getpid()))
        LOCK_FILE.flush()
        # Log the lock file path for debugging
        logger.info(f"INFO: Lock file created at: {os.path.abspath(LOCK_FILE_PATH)}")
        
        try:
            # Try to get an exclusive lock
            fcntl.flock(LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)
            logger.info(f"Acquired lock file for PID {os.getpid()}")
            return False
        except IOError:
            # This shouldn't happen since we just created the file,
            # but handle it just in case
            logger.error("Failed to acquire lock on newly created file")
            LOCK_FILE.close()
            LOCK_FILE = None
            return True
    except IOError as e:
        # Lock already held by another process
        logger.info(f"Lock already held by another process: {e}")
        if LOCK_FILE:
            LOCK_FILE.close()
            LOCK_FILE = None
        return True
    except Exception as e:
        logger.error(f"Error in file locking mechanism: {e}")
        # Fall back to process-based check if file locking fails
        if LOCK_FILE:
            LOCK_FILE.close()
            LOCK_FILE = None
        return _check_already_running_by_process()

def _check_already_running_by_process():
    """Fallback method to check if Syllablaze is already running by process name"""
    import psutil
    current_pid = os.getpid()
    count = 0
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Check if this is a Python process
            if proc.info['name'] == 'python' or proc.info['name'] == 'python3':
                # Check if it's running syllablaze
                cmdline = proc.info['cmdline']
                if cmdline and any('syllablaze' in cmd for cmd in cmdline):
                    # Don't count the current process
                    if proc.info['pid'] != current_pid:
                        count += 1
                        logger.info(f"Found existing Syllablaze process: PID {proc.info['pid']}")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    return count > 0

def cleanup_lock_file():
    """Clean up lock file when application exits"""
    global LOCK_FILE
    if LOCK_FILE:
        try:
            import fcntl
            # Release the lock
            fcntl.flock(LOCK_FILE, fcntl.LOCK_UN)
            LOCK_FILE.close()
            # Remove the lock file
            if os.path.exists(LOCK_FILE_PATH):
                os.remove(LOCK_FILE_PATH)
            LOCK_FILE = None
            logger.info("Released application lock file at exit")
        except Exception as e:
            logger.error(f"Error releasing lock file: {e}")

# Suppress GTK module error messages
os.environ['GTK_MODULES'] = ''

def main():
    import sys
    
    # We won't use signal handlers since they don't seem to work with Qt
    # Instead, we'll use a more direct approach
    
    try:
        
        # Check if already running
        if check_already_running():
            print("Syllablaze is already running. Only one instance is allowed.")
            # Exit gracefully without trying to show a QMessageBox
            return 1
            
        # Initialize QApplication after checking for another instance
        app = QApplication(sys.argv)
        setup_application_metadata()
        
        # Show loading window first
        loading_window = LoadingWindow()
        loading_window.show()
        app.processEvents()  # Force update of UI
        loading_window.set_status("Checking system requirements...")
        app.processEvents()  # Force update of UI
        
        # Check if system tray is available
        if not TrayRecorder.isSystemTrayAvailable():
            QMessageBox.critical(None, "Error",
                "System tray is not available. Please ensure your desktop environment supports system tray icons.")
            return 1
        
        # Create tray icon but don't initialize yet
        tray = TrayRecorder()
        
        # Connect loading window to tray initialization
        tray.initialization_complete.connect(loading_window.close)
        
        # Check dependencies in background
        loading_window.set_status("Checking dependencies...")
        app.processEvents()  # Force update of UI
        if not check_dependencies():
            return 1
        
        # Ensure the application doesn't quit when last window is closed
        app.setQuitOnLastWindowClosed(False)
        
        # Initialize tray in background
        QTimer.singleShot(100, lambda: initialize_tray(tray, loading_window, app))
        
        # Instead of using app.exec(), we'll use a custom event loop
        # that allows us to check for keyboard interrupts
        try:
            # Start the Qt event loop
            exit_code = app.exec()
            # Clean up before exiting
            cleanup_lock_file()
            return exit_code
        except KeyboardInterrupt:
            # This will catch Ctrl+C
            print("\nReceived Ctrl+C, exiting...")
            cleanup_lock_file()
            return 0
        
    except Exception as e:
        logger.exception("Failed to start application")
        QMessageBox.critical(None, "Error",
            f"Failed to start application: {str(e)}")
        return 1

def initialize_tray(tray, loading_window, app):
    try:
        # Initialize basic tray setup
        loading_window.set_status("Initializing application...")
        loading_window.set_progress(10)
        app.processEvents()
        tray.initialize()
        
        # Initialize recorder
        loading_window.set_status("Initializing audio system...")
        loading_window.set_progress(25)
        app.processEvents()
        tray.recorder = AudioRecorder()
        
        # Initialize transcriber
        settings = Settings()
        model_name = settings.get('model', DEFAULT_WHISPER_MODEL)
        
        # Check if model is downloaded
        try:
            model_info, _ = get_model_info()
            if model_name in model_info and not model_info[model_name]['is_downloaded']:
                loading_window.set_status(f"Whisper model '{model_name}' is not downloaded. Using default model.")
                loading_window.set_progress(40)
                app.processEvents()
                # Set model to default if current model is not downloaded
                settings.set('model', DEFAULT_WHISPER_MODEL)
                model_name = DEFAULT_WHISPER_MODEL
        except Exception as model_error:
            logger.error(f"Error checking model info: {model_error}")
            loading_window.set_status("Error checking model info. Using default model.")
            loading_window.set_progress(40)
            app.processEvents()
            # Set model to default if there was an error
            settings.set('model', DEFAULT_WHISPER_MODEL)
            model_name = DEFAULT_WHISPER_MODEL
        
        loading_window.set_status(f"Loading Whisper model: {model_name}")
        loading_window.set_progress(50)
        app.processEvents()
        
        try:
            tray.transcriber = WhisperTranscriber()
            loading_window.set_progress(80)
            app.processEvents()
        except Exception as e:
            logger.error(f"Failed to initialize transcriber: {e}")
            QMessageBox.critical(None, "Error",
                f"Failed to load Whisper model: {str(e)}\n\nPlease check Settings to download the model.")
            loading_window.set_progress(80)
            app.processEvents()
            # Create transcriber anyway, it will handle errors during transcription
            tray.transcriber = WhisperTranscriber()
        
        # Connect signals
        loading_window.set_status("Setting up signal handlers...")
        loading_window.set_progress(90)
        app.processEvents()
        tray.recorder.volume_updated.connect(tray.update_volume_meter)
        tray.recorder.recording_finished.connect(tray.handle_recording_finished)
        tray.recorder.recording_error.connect(tray.handle_recording_error)
        
        tray.transcriber.transcription_progress.connect(tray.update_processing_status)
        tray.transcriber.transcription_progress_percent.connect(tray.update_processing_progress)
        tray.transcriber.transcription_finished.connect(tray.handle_transcription_finished)
        tray.transcriber.transcription_error.connect(tray.handle_transcription_error)
        
        # Make tray visible
        loading_window.set_status("Starting application...")
        loading_window.set_progress(100)
        app.processEvents()
        
        # Make tray visible
        tray.setVisible(True)
        
        # Signal completion
        tray.initialization_complete.emit()
        
    except Exception as e:
        logger.error(f"Initialization failed: {e}")
        QMessageBox.critical(None, "Error", f"Failed to initialize application: {str(e)}")
        loading_window.close()
        # Ensure the application can be closed with CTRL+C
        app.quit()

if __name__ == "__main__":
    sys.exit(main()) 