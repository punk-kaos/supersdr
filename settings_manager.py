import os
import json
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from typing import Any

class SettingsManager(QObject):
    """
    Manages application settings, loading from and saving to a JSON file.
    Uses a QTimer for deferred saving to prevent crashes related to file I/O
    during rapid UI events.
    """
    settings_changed = pyqtSignal() # Signal emitted when settings are updated internally

    def __init__(self, parent=None, settings_file_name="supersdr_settings.json"):
        super().__init__(parent)
        self._settings_file_path = os.path.join(os.path.expanduser("~"), settings_file_name)
        self._settings_cache = {}
        self._load_settings()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._perform_save)
        
        print(f"SettingsManager initialized. File: {self._settings_file_path}")

    def _load_settings(self):
        """Loads settings from the JSON file into the cache."""
        if os.path.exists(self._settings_file_path):
            try:
                with open(self._settings_file_path, 'r') as f:
                    self._settings_cache = json.load(f)
                print(f"Settings loaded from {self._settings_file_path}")
            except json.JSONDecodeError as e:
                print(f"Error decoding settings JSON: {e}. Using default settings.")
                self._settings_cache = {}
            except Exception as e:
                print(f"Error loading settings file: {e}. Using default settings.")
                self._settings_cache = {}
        else:
            print(f"Settings file not found: {self._settings_file_path}. Using default settings.")
            self._settings_cache = {}

    def _perform_save(self):
        """Performs the actual write of settings from cache to disk."""
        try:
            with open(self._settings_file_path, 'w') as f:
                json.dump(self._settings_cache, f, indent=2)
            print(f"Settings saved to {self._settings_file_path}")
        except Exception as e:
            print(f"Error saving settings to file: {e}")

    def get_value(self, key: str, default: Any = None) -> Any:
        """Retrieves a setting value from the cache."""
        return self._settings_cache.get(key, default)

    def set_value(self, key: str, value: Any):
        """Sets a setting value in the cache and schedules a save."""
        if self._settings_cache.get(key) != value:
            self._settings_cache[key] = value
            self.settings_changed.emit() # Notify if cache changed
            self._save_timer.start(500) # Defer save by 500ms
            print(f"Setting '{key}' updated to '{value}'. Save scheduled.")

    def get_cat_sync_checkbox_state(self, key: str) -> bool:
        """Specialized getter for CAT sync checkbox states."""
        return self.get_value(key, False)

    def set_cat_sync_checkbox_state(self, key: str, value: bool):
        """Specialized setter for CAT sync checkbox states."""
        self.set_value(key, value)

    def print_all_settings(self):
        """Prints all current settings in the cache."""
        print("Current Settings:")
        for key, value in self._settings_cache.items():
            print(f"  {key}: {value}")

if __name__ == '__main__':
    # Simple test of the SettingsManager
    from PyQt5.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)
    
    # Create a manager instance for testing
    sm = SettingsManager(settings_file_name="test_supersdr_settings.json")
    sm.print_all_settings()

    # Test setting values
    sm.set_cat_sync_checkbox_state("cat_sync_kiwi_to_radio_freq", True)
    sm.set_value("general_volume", 75)
    sm.set_cat_sync_checkbox_state("cat_sync_radio_to_kiwi_mode", True)
    
    # Values should be in cache immediately
    print("\nSettings after update calls (before deferred save):")
    sm.print_all_settings()
    
    # The actual save happens after the timer times out
    print("\nWaiting for deferred save...")
    QTimer.singleShot(1000, lambda: sys.exit(app.quit())) # Quit after 1 second
    app.exec_()
    
    # Verify file content after app exits (in a real scenario you'd restart app)
    print("\nVerifying saved settings (simulated restart):")
    sm_reloaded = SettingsManager(settings_file_name="test_supersdr_settings.json")
    sm_reloaded.print_all_settings()
    
    # Clean up test file
    if os.path.exists(sm_reloaded._settings_file_path):
        os.remove(sm_reloaded._settings_file_path)
        print(f"\nCleaned up test settings file: {sm_reloaded._settings_file_path}")
