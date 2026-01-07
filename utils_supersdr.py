import random
import struct
import array
import math
from collections import deque, defaultdict
import pickle
import threading, queue
import socket
import time
from datetime import datetime, timedelta
import sys
import urllib
if sys.version_info > (3,):
    buffer = memoryview
    def bytearray2str(b):
        return b.decode('ascii')
else:
    def bytearray2str(b):
        return str(b)

import numpy as np
from scipy.signal import resample_poly, welch

import sounddevice as sd
import wave
import string

from PyQt5.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QListWidget, QLabel, QFrame
from PyQt5.QtCore import Qt, pyqtSignal

from qrz_utils import *
from kiwi import wsclient
import mod_pywebsocket.common
from mod_pywebsocket.stream import Stream
from mod_pywebsocket.stream import StreamOptions
from mod_pywebsocket._stream_base import ConnectionTerminatedException

VERSION = "v3.14"

class flags():
    # global mutable flags
    auto_mode = True
    input_freq_flag = False
    input_server_flag = False
    show_help_flag =  False
    s_meter_show_flag = False
    show_eibi_flag = False
    show_mem_flag = True
    show_dxcluster_flag = False
    connect_dxcluster_flag = False
    input_callsign_flag = False
    input_qso_flag = False
    dualrx_flag = False
    click_drag_flag = False
    start_drag_x = None

    wf_cat_link_flag = True
    wf_snd_link_flag = False
    cat_snd_link_flag = True

    main_sub_switch_flag = False

    tk_log_new_flag = False
    tk_log_search_flag = False
    tk_kiwi_flag = False

class audio_recording():
    CHANNELS = 1
    def __init__(self, kiwi_snd):
        self.filename = ""
        self.audio_buffer = []
        self.kiwi_snd = kiwi_snd
        self.frames = []
        self.recording_flag = False

    def start(self):
        self.filename = "supersdr_%sUTC.wav"%datetime.utcnow().isoformat().split(".")[0].replace(":", "_")
        print("start recording")
        self.audio_buffer = []
        self.recording_flag = True

    def stop(self):
        print("stop recording")
        self.recording_flag = False
        self.save()

    def save(self):
        self.wave = wave.open(self.filename, 'wb')
        self.wave.setnchannels(self.CHANNELS)
        self.wave.setsampwidth(2) # two bytes per sample (int16)
        self.wave.setframerate(self.kiwi_snd.AUDIO_RATE)
        # process audio data here
        self.wave.writeframes(b''.join(self.audio_buffer))
        self.wave.close()
        self.recording = False

from time import sleep

class dxcluster():
    # Define RGB colors locally for dxcluster
    GREEN = (0,255,0)
    YELLOW = (200,180,0)
    ORANGE = (255,140,0)
    RED = (255,0,0)
    GREY = (200,200,200)

    CLEANUP_TIME = 120
    UPDATE_TIME = 10
    SPOT_TTL_BASETIME = 600
    color_dict = {0: GREEN, SPOT_TTL_BASETIME: YELLOW, SPOT_TTL_BASETIME*2: ORANGE, SPOT_TTL_BASETIME*3: RED, SPOT_TTL_BASETIME*4: GREY}

    def __init__(self, mycall_):
        if mycall_ == "":
            raise
        self.mycall = mycall_
        host, port = 'dxfun.com', 8000
        self.server = (host, port)
        self.spot_dict = {}
        self.visible_stations = []
        self.terminate = False
        self.failed_counter = 0
        self.update_now = False

    def disconnect(self):
        self.terminate = True
        try:
            self.sock.shutdown(1)
            self.sock.close()
        except:
            pass
        print("DXCLuster disconnected!")
        
    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connected = False
        while not connected:
            print("Connecting to: %s:%d" % self.server)
            try:
                self.sock.connect(self.server)
            except:
                print('Impossibile to connect')
                sleep(5)     
            else:
                # self.sock.settimeout(1)
                print('Connected!!!')
                connected = True
        self.send(self.mycall)
        self.time_to_live = self.SPOT_TTL_BASETIME*5 # seconds for a spot to live
        self.last_update = datetime.utcnow()
        self.last_cleanup = datetime.utcnow()

    def send(self, msg):
        msg = msg + '\n'
        self.sock.send(msg.encode())

    def keepalive(self):
        try:
            self.send(chr(8))
            self.receive()
        except:
            print("DX cluster failed to reply to keepalive msg")

    def receive(self):
        try:
            msg = self.sock.recv(2048)
            msg = msg.decode("utf-8")
        except:
            msg = None
            #print("DX cluster msg decode failed")
        return msg

    def decode_spot(self, line):
        els = line.split("  ")
        els = [x for x in els if x]
        spotter = els[0][6:].replace(":", "")
        utc = datetime.utcnow()
        try:
            qrg = float(els[1].strip())
            callsign = els[2].strip()
            dxde_callsign = els[0][6:].split(":")[0]
            print("New SPOT:", utc.strftime('%H:%M:%SZ'), qrg, "kHz", callsign, "DX de", dxde_callsign)
        except:
            qrg, callsign, utc = None, None, None
            print("DX cluster msg decode failed: %s"%els)
        return qrg, callsign, utc, els

    def clean_old_spots(self):
        now  = datetime.utcnow()
        del_list = []
        for spot_id in self.spot_dict.keys():
            spot_utc = self.spot_dict[spot_id][2]
            duration = now - spot_utc
            duration_in_s = duration.total_seconds()
            if duration_in_s > self.time_to_live:
                del_list.append(spot_id)
        for spot_id in del_list:
            del self.spot_dict[spot_id]
        print("Number of spots in memory:", len(self.spot_dict.keys()))

    def run(self, kiwi_wf):
        self.connect()
        while not self.terminate:
            try:
                dx_cluster_msg = self.receive()
            except:
                continue

            spot_str = "%s"%dx_cluster_msg
            stored_something_flag = False
            for line in spot_str.replace("\x07", "").split("\n"):
                if "DX de " in line:
                    qrg, callsign, utc, spot_msg = self.decode_spot(line)
                    if qrg and callsign:
                        self.store_spot(qrg, callsign, utc, spot_msg)
                        stored_something_flag = True
                    else:
                        continue
            if stored_something_flag:           
                self.update_now = True

            delta_t = (datetime.utcnow() - self.last_cleanup).total_seconds()
            if delta_t > self.CLEANUP_TIME: # cleanup db and keepalive msg
                self.keepalive()
                self.clean_old_spots()
                self.last_cleanup = datetime.utcnow()
                # print("DXCLUST: cleaned old spots")
            delta_t = (datetime.utcnow() - self.last_update).total_seconds()
            if delta_t > self.UPDATE_TIME or self.update_now:
                self.get_stations(kiwi_wf.start_f_khz, kiwi_wf.end_f_khz)
                # print("DXCLUST: updated visible spots")
                self.last_update = datetime.utcnow()
                self.update_now = False
        print("Exited from DXCLUSTER loop")

    def store_spot(self, qrg_, callsign_, utc_, spot_msg_):
        spot_id = next(self.unique_id()) # create a unique hash for each spot
        self.spot_dict[spot_id] = (callsign_, qrg_, utc_, spot_msg_) # save spots as elements of a dictionary with hashes as keys

    def get_stations(self, start_f, end_f):
        count_dupes_dict = defaultdict(list)
        self.visible_stations = []
        for spot_id in self.spot_dict.keys():
            (callsign_, qrg_, utc_, spot_msg_) = self.spot_dict[spot_id]
            if start_f < qrg_ < end_f:
                count_dupes_dict[callsign_].append(spot_id)
                self.visible_stations.append(spot_id)

        self.visible_stations = sorted(self.visible_stations, key=lambda spot_id: self.spot_dict[spot_id][1])
        for call in count_dupes_dict.keys():
            same_call_list = []
            if len(count_dupes_dict[call])>1:
                same_call_list = sorted([spot_id for spot_id in count_dupes_dict[call]], key = lambda spot_id: self.spot_dict[spot_id][2])
                for spot_id in same_call_list[:-1]:
                    self.visible_stations.remove(spot_id)
                    del self.spot_dict[spot_id]

    def unique_id(self):
        seed = random.getrandbits(32)
        while True:
           yield seed
           seed += 1


class filtering():
    def __init__(self, fl, fs):
        b = fl/fs
        N = int(np.ceil((4 / b)))
        if not N % 2: N += 1  # Make sure that N is odd.
        self.n_tap = N
        self.h = np.sinc(2. * fl / fs * (np.arange(N) - (N - 1) / 2.))
        w = np.blackman(N)
        # Multiply sinc filter by window.
        self.h = self.h * w
        # Normalize to get unity gain.
        self.h = self.h / np.sum(self.h)

    def lowpass(self, signal):
        filtered_sig = np.convolve(signal, self.h, mode="valid")
        return filtered_sig


class memory():
    def __init__(self):
        self.mem_list = deque([], 10)
        self.index = 0
        # try:
        #     self.load_from_disk()
        # except:
        #     pass
        # self.index = len(self.mem_list)

    def write_mem(self, freq, radio_mode, delta_low, delta_high):
        self.mem_list.append((round(freq, 3), radio_mode, delta_low, delta_high))
    
    def recall_mem(self):
        if len(self.mem_list)>0:
            self.index += 1
            self.index %= len(self.mem_list)
            return self.mem_list[self.index]
        else:
            return None
    
    def reset_all_mem(self):
        self.mem_list = deque([], 10)

    def save_to_disk(self):
        current_mem = self.mem_list
        self.load_from_disk()
        self.mem_list += current_mem
        self.mem_list = list(set(self.mem_list))
        try:
            with open("supersdr.memory", "wb") as fd:
                pickle.dump(self.mem_list, fd)
        except:
            print("Cannot save memory file!")

    def load_from_disk(self):
        try:
            with open("supersdr.memory", "rb") as fd:
                self.mem_list = pickle.load(fd)
        except:
            print("No memory file found!")

class KiwiSDRChooserDialog(QDialog):
    def __init__(self, kiwi_list_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose a KiwiSDR")
        self.setFixedSize(400, 400)

        self.kiwi_list_manager = kiwi_list_manager
        self.kiwi_host = None
        self.kiwi_port = None
        self.kiwi_password = None
        self.connect_new_flag = False

        main_layout = QVBoxLayout(self)

        label = QLabel("Choose KiwiSDR")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("font-size: 18px; font-weight: bold;")
        main_layout.addWidget(label)

        input_layout = QHBoxLayout()
        input_label = QLabel("Host:Port:Password")
        self.entry_kiwi = QLineEdit()
        self.entry_kiwi.setPlaceholderText("e.g., sdr.example.com:8073:mypass")
        self.entry_kiwi.returnPressed.connect(self._on_connect_clicked)
        input_layout.addWidget(input_label)
        input_layout.addWidget(self.entry_kiwi)
        main_layout.addLayout(input_layout)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_list_item_double_clicked)
        main_layout.addWidget(self.list_widget)

        button_layout = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.connect_save_btn = QPushButton("Save and Connect")
        self.connect_save_btn.clicked.connect(lambda: self._on_connect_clicked(save_flag=True))
        self.reload_btn = QPushButton("Reload")
        self.reload_btn.clicked.connect(self.reload_and_refresh)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self.connect_btn)
        button_layout.addWidget(self.connect_save_btn)
        button_layout.addWidget(self.reload_btn)
        button_layout.addWidget(self.cancel_btn)
        main_layout.addLayout(button_layout)

        self.refresh_list()
        self.entry_kiwi.setFocus()

    def refresh_list(self):
        self.list_widget.clear()
        for idx, kiwi_record in enumerate(self.kiwi_list_manager.kiwi_list):
            host, port, password, comments = kiwi_record
            display_string = f"{idx}. {host}:{port}"
            if comments:
                display_string += f" ({comments})"
            self.list_widget.addItem(display_string)

    def reload_and_refresh(self):
        self.kiwi_list_manager.load_from_disk()
        self.refresh_list()

    def _on_connect_clicked(self, save_flag=False):
        kiwi_data_str = self.entry_kiwi.text().strip()

        if not kiwi_data_str and self.list_widget.currentRow() != -1:
            # If input empty, use selected list item
            idx = self.list_widget.currentRow()
            if 0 <= idx < len(self.kiwi_list_manager.kiwi_list):
                self.kiwi_host, self.kiwi_port, self.kiwi_password, _ = self.kiwi_list_manager.kiwi_list[idx]
                self.connect_new_flag = True
                self.accept()
                return
            else:
                print("Invalid selection.")
                return

        elif kiwi_data_str:
            # Parse input string: host:port:password
            parts = kiwi_data_str.split(":")
            if len(parts) >= 1 and parts[0]:
                self.kiwi_host = parts[0]
                try:
                    self.kiwi_port = int(parts[1]) if len(parts) >= 2 and parts[1] else self.kiwi_list_manager.default_port
                except ValueError:
                    print("Invalid port number. Using default.")
                    self.kiwi_port = self.kiwi_list_manager.default_port
                self.kiwi_password = parts[2] if len(parts) >= 3 else self.kiwi_list_manager.default_password
                
                if save_flag:
                    self.kiwi_list_manager.save_to_disk(self.kiwi_host, self.kiwi_port, self.kiwi_password, "")
                
                self.connect_new_flag = True
                self.accept()
                return
            else:
                print("Invalid input format. Please use host:port:password or select from list.")
                return
        
        print("No KiwiSDR selected or entered.")
        self.connect_new_flag = False

    def _on_list_item_double_clicked(self, item):
        idx = self.list_widget.row(item)
        if 0 <= idx < len(self.kiwi_list_manager.kiwi_list):
            self.kiwi_host, self.kiwi_port, self.kiwi_password, _ = self.kiwi_list_manager.kiwi_list[idx]
            self.connect_new_flag = True
            self.accept()
        else:
            print("Error: Double-clicked item not found in list data.")

class kiwi_list():
    def __init__(self):
        self.kiwi_list_filename = "kiwi.list"
        self.kiwi_host = ""
        self.kiwi_port = ""
        self.kiwi_password = ""
        self.default_port = 8073
        self.default_password = ""
        self.connect_new_flag = False

        try:
            self.load_from_disk()
        except:
            pass

    def save_to_disk(self, host, port, password, comments):
        no_file_flag = False
        try:
            with open(self.kiwi_list_filename, encoding="latin") as fd:
                data = fd.readlines()
            if len(data) == 0:
                no_file_flag = True
        except FileNotFoundError:
            no_file_flag = True
        except Exception as e:
            print(f"Error checking kiwi list file: {e}")
            return

        try:
            with open(self.kiwi_list_filename, "a") as fd:
                if no_file_flag:
                    fd.write("KIWIHOST;KIWIPORT;KIWIPASSWORD;COMMENTS\n")
                fd.write(f"{host};{port};{password};{comments}\n")
            self.load_from_disk()
        except Exception as e:
            print(f"Cannot save kiwi list to disk! Error: {e}")

    def load_from_disk(self):
        self.kiwi_list = []
        try:
            with open(self.kiwi_list_filename, encoding="latin") as fd:
                data = fd.readlines()

            if not data:
                print("Kiwi list file is empty!")
                return

            for row in data[1:]:
                row = row.strip()
                if not row or row.startswith("#"):
                    continue
                
                fields = row.split(";")
                if len(fields) < 1:
                    continue

                host = fields[0]
                try:
                    port = int(fields[1]) if len(fields) > 1 and fields[1] else self.default_port
                except ValueError:
                    port = self.default_port
                
                password = fields[2] if len(fields) > 2 else ""
                comments = fields[3] if len(fields) > 3 else ""
                self.kiwi_list.append((host, port, password, comments))
        except FileNotFoundError:
            print("No kiwi list file found!")
        except Exception as e:
            print(f"Error loading kiwi list from disk! Error: {e}")
            self.kiwi_list = []
    
    def choose_kiwi_dialog(self):
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
            
        dialog = KiwiSDRChooserDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.kiwi_host = dialog.kiwi_host
            self.kiwi_port = dialog.kiwi_port
            self.kiwi_password = dialog.kiwi_password
            self.connect_new_flag = dialog.connect_new_flag
            return self.kiwi_host, self.kiwi_port, self.kiwi_password, self.connect_new_flag
        else:
            self.connect_new_flag = False
            return None, None, None, False
