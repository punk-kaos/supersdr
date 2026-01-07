import string
import time
from qrz_utils import *

import utils_supersdr 

from kiwi import wsclient
import mod_pywebsocket.common
from mod_pywebsocket.stream import Stream
from mod_pywebsocket.stream import StreamOptions
from mod_pywebsocket._stream_base import ConnectionTerminatedException

VERSION = "v3.14"

TENMHZ = 10000
HELP_MESSAGE_LIST = ["SuperSDR %s HELP" % VERSION,
        "",
        "- LEFT/RIGHT: move KIWI RX freq +/- 1kHz (+SHIFT: x10)",
        "- PAGE UP/DOWN: move WF freq +/- SPAN/4",
        "- UP/DOWN: zoom in/out by a factor 2X",
        "- U/L/C/A: switch to USB, LSB, CW, AM",
        "- J/K/O: tune RX low/high cut (SHIFT inverts, try CTRL!), O resets",
        "- CTRL+O: reset window size to native 1024 bins",
        "- G/H: inc/dec spectrum and WF averaging to improve SNR",
        "- ,/.(+SHIFT) change high(low) clip level for spectrum and WF",
        "- E: start/stop audio recording",
        "- F: enter frequency with keyboard",
        "- W/R: Write/Recall quick cyclic memory (up to 10)",
        "- SHIFT+W: Save all memories to disk",
        "- SHIFT+R: Delete all stored memories",
        "- V/B: up/down volume 10%, SHIFT+V mute/unmute",
        "- M: S-METER show/hide",
        "- Y: activate SUB RX or switch MAIN/SUB RX (+SHIFT kills it)",
        "- S: SYNC CAT and KIWI RX ON/OFF -> SPLIT mode for RTX",
        "- Z: Center KIWI RX, shift WF instead",
        "- SPACE: FORCE SYNC of WF to RX if no CAT, else sync to CAT",
        "- X: AUTO MODE ON/OFF depending on amateur/broadcast band",
        "- D: connect/disconnect from DXCLUSTER server",
        "- I: show/hide EIBI database stations",
        "- Q: switch to a different KIWI server",
        "- 1/2 & 3: adjust AGC threshold (+SHIFT decay), 3 WF autoscale",
        "- 0/9: [LOGGER] add QSO to log / open search QSO dialog",
        "- 4: enable/disable spectrum filling",
        "- 5/6: pan audio left/right for active RX",
        "- SHIFT+ESC: quits"]

CW_PITCH = 0.6
LOW_CUT_SSB = 30
HIGH_CUT_SSB = 3000
LOW_CUT_CW = int(CW_PITCH*1000-200)
HIGH_CUT_CW = int(CW_PITCH*1000+200)
HIGHLOW_CUT_AM = 6000
delta_low, delta_high = 0., 0.
default_kiwi_port = 8073
default_kiwi_password = ""

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
import urllib.request 
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

def get_auto_mode(freq):
    if freq < TENMHZ:
        return "USB"
    else:
        return "LSB"

class beacons:
    def __init__(self):
        self.freq_dict = {}
        self.beacons_dict = {}
        self.beacons_list = []

    def which_beacons(self):
        pass

class memory():
    def __init__(self):
        self.mem_list = []
        self.index = 0

    def add_mem(self, freq, mode, delta_low, delta_high):
        self.mem_list.append((round(freq, 3), mode, delta_low, delta_high))

    def recall_mem(self, idx):
        if 0 <= idx < len(self.mem_list):
            self.index = idx
            return self.mem_list[idx]
        return None

class eibi_db():
    def __init__(self):
        self.station_dict = {}
        self.visible_stations = []

    def get_stations(self, start_f, end_f):
        self.visible_stations = []
        self.station_dict = {}

class kiwi_sdr:
    kiwi_status_dict = {}
    active = True
    offline = False
    users = 0
    users_max = 4
    gps = (None, None)
    qth = ""
    freq_offset = 0
    antenna = ""
    kiwi_name = ""
    min_freq, max_freq = None, None

    def __init__(self, host, port, verbose_flag=False):
        url = "http://%s:%d/status" % (host, port)
        try:
            file = urllib.request.urlopen(url, timeout=10)
        except Exception as e:
            print(f"Error fetching KiwiSDR status: {e}")
            raise

        for line in file:
            decoded_line = line.decode("utf-8").rstrip()
            key, value = decoded_line.split("=")[0], decoded_line.split("=")[1]
            self.kiwi_status_dict[key] = value

        try:
            self.users = int(self.kiwi_status_dict["users"])
            self.users_max = int(self.kiwi_status_dict["users_max"])
            self.antenna = self.kiwi_status_dict["antenna"]
            self.kiwi_name = self.kiwi_status_dict["name"]
            self.qth = self.kiwi_status_dict["loc"]
            self.active = True if self.kiwi_status_dict["status"] in ["active", "private"] else False
            self.offline = False if self.kiwi_status_dict["offline"]=="no" else True

            gps_str = self.kiwi_status_dict.get("gps", "")
            gps_parts = gps_str.split(", ")
            if len(gps_parts) >= 2:
                try:
                    lat_str = gps_parts[0][1:]
                    lon_str = gps_parts[1][:-1]
                    self.gps = (float(lat_str), float(lon_str))
                except (ValueError, IndexError) as e:
                    print(f"Error parsing GPS: {e}")
                    self.gps = (None, None)
            else:
                self.gps = (None, None)

            bands_str = self.kiwi_status_dict.get("bands", "")
            bands_parts = bands_str.split("-")
            if len(bands_parts) >= 2:
                try:
                    self.min_freq = float(bands_parts[0])
                    self.max_freq = float(bands_parts[1])
                except (ValueError, IndexError) as e:
                    print(f"Error parsing bands: {e}")
                    self.min_freq, self.max_freq = None, None
            else:
                self.min_freq, self.max_freq = None, None

            try:
                self.freq_offset = float(self.kiwi_status_dict["freq_offset"])
            except:
                if verbose_flag:
                    print("Some status parameters not found! Old firmware?")
                self.freq_offset = 0
        except Exception as e:
            if verbose_flag:
                print(f"Error in kiwi_sdr init: {e}")
            raise
        if verbose_flag:
            print(self.kiwi_status_dict)

class kiwi_waterfall():
    MAX_FREQ = 30000
    CENTER_FREQ = int(MAX_FREQ/2)
    MAX_ZOOM = 14
    WF_BINS = 1024
    MAX_FPS = 23
    MIN_DYN_RANGE = 40.
    CLIP_LOWP, CLIP_HIGHP = 40., 100
    delta_low_db, delta_high_db = 0, 0
    low_clip_db, high_clip_db = -120, -60
    wf_min_db, wf_max_db = low_clip_db, low_clip_db+MIN_DYN_RANGE
    kiwi_wf_timestamp = None
    wf_buffer_len = 3
    
    def __init__(self, host_, port_, pass_, zoom_, freq_, eibi, disp):
        self.eibi = eibi
        self.host = host_
        self.port = port_
        self.password = pass_
        print ("KiwiSDR Server: %s:%d" % (self.host, self.port))
        self.zoom = zoom_
        self.freq = freq_
        self.averaging_n = 1
        self.wf_auto_scaling = True
        self.BINS2PIXEL_RATIO = disp.DISPLAY_WIDTH / self.WF_BINS

        self.old_averaging_n = self.averaging_n
        self.dynamic_range = self.MIN_DYN_RANGE
        
        self.wf_white_flag = False
        self.terminate = False
        self.run_index = 0

        if not self.freq:
            self.freq = 14200
        self.tune = self.freq
        self.radio_mode = "USB"
        
        print ("Zoom factor:", self.zoom)
        self.span_khz = self.zoom_to_span()
        self.start_f_khz = self.start_freq()
        self.end_f_khz = self.end_freq()
        
        self.div_list = []
        self.subdiv_list = []
        self.min_bin_spacing = 100
        self.space_khz = 10
        self.counter, self.actual_freq = self.start_frequency_to_counter(self.start_f_khz)
        self.socket = None
        self.wf_stream = None
        self.wf_color = None
        self.freq_offset = 0

        kiwi_sdr_status = kiwi_sdr(host_, port_, True)
        print(kiwi_sdr_status.users, kiwi_sdr_status.users_max)
        if kiwi_sdr_status.users == kiwi_sdr_status.users_max:
            print ("Too many users!")
            # raise Exception()
        elif kiwi_sdr_status.offline or not kiwi_sdr_status.active:
            print ("KiwiSDR offline or under maintenance! Failed to connect!")
            raise Exception()
        else:
            self.freq_offset = kiwi_sdr_status.freq_offset/1000.0

        print ("Trying to contact %s..."%self.host)
        try:
            self.socket = socket.socket()
            self.socket.connect((self.host, self.port))
            print ("Socket open...")
        except:
            print ("Failed to connect")
            raise Exception()
        
        self.start_stream()
        
        while True:
            msg = self.wf_stream.receive_message()
            if msg:
                if bytearray2str(msg[0:3]) == "W/F":
                    break
                elif "MSG center_freq" in bytearray2str(msg):
                    els = bytearray2str(msg[4:]).split()                
                    self.MAX_FREQ = int(int(els[1].split("=")[1])/1000)
                    self.CENTER_FREQ = int(int(self.MAX_FREQ)/2)
                    self.span_khz = self.zoom_to_span()
                    self.start_f_khz = self.start_freq()
                    self.end_f_khz = self.end_freq()
                    self.counter, self.actual_freq = self.start_frequency_to_counter(self.start_f_khz)
                elif "MSG wf_fft_size" in bytearray2str(msg):
                    els = bytearray2str(msg[4:]).split()
                    self.MAX_ZOOM = int(els[3].split("=")[1])
                    self.WF_BINS = int(els[0].split("=")[1])
                    self.MAX_FPS = int(els[2].split("=")[1])
                
        self.bins_per_khz = self.WF_BINS / self.span_khz
        self.wf_data = np.zeros((disp.WF_HEIGHT, self.WF_BINS))
        self.wf_data_tmp = deque([], self.wf_buffer_len)

        self.avg_spectrum_deque = deque([], self.averaging_n)

    def gen_div(self):
        self.space_khz = 10
        self.div_list = []
        self.subdiv_list = []
        self.div_list = []
        f_s = int(self.start_f_khz)
        f_e = int(self.end_f_khz)
    
        while self.div_list == [] and self.subdiv_list == []:
            if self.bins_per_khz*self.space_khz > self.min_bin_spacing:
                for f in range(f_s, f_e+1):
                    if not f%self.space_khz:
                        fbin = int(self.offset_to_bin(f-self.start_f_khz))
                        self.div_list.append(fbin)

            if self.bins_per_khz*self.space_khz/10 > self.min_bin_spacing/10:
                for f in range(f_s, f_e+1):
                    if not f%(self.space_khz/10):
                        fbin = int(self.offset_to_bin(f-self.start_f_khz))
                        self.subdiv_list.append(fbin)
            self.space_khz *= 10                

    def start_stream(self):
        self.kiwi_wf_timestamp = int(time.time())
        uri = '/%d/%s' % (self.kiwi_wf_timestamp, 'W/F')

        try:
            handshake_wf = wsclient.ClientHandshakeProcessor(self.socket, self.host, self.port)
            handshake_wf.handshake(uri)
            request_wf = wsclient.ClientRequest(self.socket)
        except:
            return None
        request_wf.ws_version = mod_pywebsocket.common.VERSION_HYBI13
        stream_option_wf = StreamOptions()
        stream_option_wf.mask_send = True
        stream_option_wf.unmask_receive = False
        self.wf_stream = Stream(request_wf, stream_option_wf)
        print(self.wf_stream)
        if self.wf_stream:
            print ("Waterfall data stream active...")
        msg_list = ['SET auth t=kiwi p=%s ipl=%s'%(self.password, self.password), 'SET zoom=%d start=%d'%(self.zoom,self.counter),\
        'SET maxdb=-10 mindb=-110', 'SET wf_speed=4', 'SET wf_comp=0', "SET interp=13"]
        for msg in msg_list:
            self.wf_stream.send_message(msg)
        print ("Starting to retrieve waterfall data...")

    def zoom_to_span(self):
            assert(self.zoom >= 0 and self.zoom <= self.MAX_ZOOM)
            self.span_khz = self.MAX_FREQ / 2**self.zoom
            return self.span_khz

    def start_frequency_to_counter(self, start_frequency_):
        assert(start_frequency_ >= 0 and start_frequency_ <= self.MAX_FREQ)
        self.counter = round(start_frequency_/self.MAX_FREQ * 2**self.MAX_ZOOM * self.WF_BINS)
        start_frequency_ = self.counter * self.MAX_FREQ / self.WF_BINS / 2**self.MAX_ZOOM
        return self.counter, start_frequency_

    def start_freq(self):
        self.start_f_khz = self.freq - self.span_khz/2
        return self.start_f_khz

    def end_freq(self):
        self.end_f_khz = self.freq + self.span_khz/2
        return self.end_f_khz

    def offset_to_bin(self, offset_khz_):
        bins_per_khz_ = self.WF_BINS / self.span_khz
        return bins_per_khz_ * (offset_khz_)

    def bins_to_khz(self, bins_):
        bins_per_khz_ = self.WF_BINS / self.span_khz
        return (1./bins_per_khz_) * (bins_) + self.start_f_khz

    def deltabins_to_khz(self, bins_):
        bins_per_khz_ = self.WF_BINS / self.span_khz
        return (1./bins_per_khz_) * (bins_)

    def receive_spectrum(self):
        msg = self.wf_stream.receive_message()
        if msg and bytearray2str(msg[0:3]) == "W/F":
            msg = msg[16:]
            self.spectrum = np.ndarray(len(msg), dtype='B', buffer=msg).astype(np.float32)
            self.keepalive()

    def spectrum_db2col(self):
        wf = self.spectrum
        wf = -(255 - wf)
        wf_db = wf - 13 + (3*self.zoom)
        wf_db[0] = wf_db[1]
        
        if self.wf_auto_scaling:
            self.low_clip_db = np.percentile(wf_db, self.CLIP_LOWP)
            self.high_clip_db = np.percentile(wf_db, self.CLIP_HIGHP)
            self.dynamic_range = max(self.high_clip_db - self.low_clip_db, self.MIN_DYN_RANGE)

        wf_color_db = (wf_db - (self.low_clip_db+self.delta_low_db))
        normal_factor_db = self.dynamic_range + self.delta_high_db
        self.wf_color = wf_color_db / (normal_factor_db-self.delta_low_db)

        self.wf_color = np.clip(self.wf_color, 0.0, 1.0)

        self.wf_min_db = self.low_clip_db + self.delta_low_db - (3*self.zoom)
        self.wf_max_db = self.low_clip_db + normal_factor_db - (3*self.zoom)

        self.wf_color *= 254
        self.wf_color = np.clip(self.wf_color, 0, 255)

    def set_freq_zoom(self, freq_, zoom_):
        self.freq = freq_
        self.zoom = zoom_
        self.zoom_to_span()
        self.start_freq()
        self.end_freq()
        if zoom_ == 0:
            self.freq = self.CENTER_FREQ
            self.start_freq()
            self.end_freq()
            self.span_khz = self.MAX_FREQ
        else:
            if self.start_f_khz<0:
                self.freq = self.zoom_to_span()/2
                self.start_freq()
                self.end_freq()
                self.zoom_to_span()
            elif self.end_f_khz>self.MAX_FREQ:
                self.freq = self.MAX_FREQ - self.zoom_to_span()/2 
                self.start_freq()
                self.end_freq()
                self.zoom_to_span()
        self.counter, actual_freq = self.start_frequency_to_counter(self.start_f_khz)
        msg = "SET zoom=%d start=%d" % (self.zoom, self.counter)
        self.wf_stream.send_message(msg)
        self.eibi.get_stations(self.start_f_khz, self.end_f_khz)
        self.bins_per_khz = self.WF_BINS / self.span_khz
        self.gen_div()

        return self.freq

    def keepalive(self):
        self.wf_stream.send_message("SET keepalive")

    def close_connection(self):
        if not self.wf_stream:
            return
        try:
            self.wf_stream.close_connection(mod_pywebsocket.common.STATUS_GOING_AWAY)
            self.socket.close()
        except Exception as e:
            print ("exception: %s" % e)

    def change_passband(self, delta_low_, delta_high_):
        lc_ = 0
        hc_ = 0
        if self.radio_mode == "USB":
            lc_ = LOW_CUT_SSB+delta_low_
            hc_ = HIGH_CUT_SSB+delta_high_
        elif self.radio_mode == "LSB":
            lc_ = -HIGH_CUT_SSB-delta_high_
            hc_ = -LOW_CUT_SSB-delta_low_
        elif self.radio_mode == "AM":
            lc_ = -HIGHLOW_CUT_AM-delta_low_
            hc_ = HIGHLOW_CUT_AM+delta_high_
        elif self.radio_mode == "CW":
            lc_ = LOW_CUT_CW+delta_low_
            hc_ = HIGH_CUT_CW+delta_high_
        self.lc, self.hc = lc_, hc_
        return lc_, hc_

    def set_white_flag(self):
        self.wf_color = np.ones_like(self.wf_color)*255
        self.wf_data[0,:] = self.wf_color

    def run(self):
        while not self.terminate:
            if self.averaging_n>1:
                self.avg_spectrum_deque = deque([], self.averaging_n)
                for avg_idx in range(self.averaging_n):
                    self.receive_spectrum()
                    self.avg_spectrum_deque.append(self.spectrum)
                self.spectrum = np.mean(self.avg_spectrum_deque, axis=0)
            else:
                self.receive_spectrum()
            self.run_index += 1

            self.spectrum_db2col()
            self.wf_data_tmp.appendleft(self.wf_color)

            if len(self.wf_data_tmp) > 0 and self.run_index > self.wf_buffer_len:
                self.wf_data[1:,:] = self.wf_data[0:-1,:]
                self.wf_data[0,:] = self.wf_data_tmp.pop()
        return


class kiwi_sound():
    FORMAT = np.int16
    CHANNELS = 2
    AUDIO_RATE = 48000
    KIWI_RATE = 12000
    SAMPLE_RATIO = int(AUDIO_RATE/KIWI_RATE)
    CHUNKS = 1
    KIWI_SAMPLES_PER_FRAME = 512

    def __init__(self, freq_, mode_, lc_, hc_, password_, kiwi_wf, buffer_len, volume_=100, host_=None, port_=None, subrx_=False):
        self.subrx = subrx_
        self.kiwi_wf = kiwi_wf
        self.host = host_ if host_ else kiwi_wf.host
        self.port = port_ if port_ else kiwi_wf.port
        self.FULL_BUFF_LEN = max(1, buffer_len)
        self.audio_buffer = queue.Queue(maxsize = self.FULL_BUFF_LEN)
        self.terminate = False
        self.volume = volume_
        self.max_rssi_before_mute = -20
        self.mute_counter = 0
        self.muting_delay = 15
        self.adc_overflow_flag = False
        self.status = None

        self.run_index = 0
        self.delta_t = 0.0
        self.rssi = -127
        self.freq = freq_
        self.radio_mode = mode_
        self.lc, self.hc = lc_, hc_

        self.on = True
        self.hang = False
        self.thresh = -80
        self.slope = 0
        self.decay_other = 4000
        self.decay_cw = 1000
        self.gain = 50
        self.min_agc_delay, self.max_agc_delay = 400, 8000
        self.decay = self.decay_other
        self.audio_balance = 0.0
        self.freq_offset = 0

        kiwi_sdr_status = kiwi_sdr(self.host, self.port)
        if kiwi_sdr_status.users == kiwi_sdr_status.users_max:
            print ("Too many users! Failed to connect!")
        elif kiwi_sdr_status.offline or not kiwi_sdr_status.active:
            print ("KiwiSDR offline or under maintenance! Failed to connect!")
            raise Exception("KiwiSDR not available")
        else:
            self.freq_offset = kiwi_sdr_status.freq_offset/1000.0

        print ("Trying to contact server...")
        try:
            self.socket = socket.socket()
            self.socket.connect((self.host, self.port))
            new_timestamp = int(time.time())
            if new_timestamp - kiwi_wf.kiwi_wf_timestamp > 5:
                kiwi_wf.kiwi_wf_timestamp = new_timestamp
            uri = '/%d/%s' % (kiwi_wf.kiwi_wf_timestamp, 'SND')
            handshake_snd = wsclient.ClientHandshakeProcessor(self.socket, self.host, self.port)
            handshake_snd.handshake(uri)
            request_snd = wsclient.ClientRequest(self.socket)
            request_snd.ws_version = mod_pywebsocket.common.VERSION_HYBI13
            stream_option_snd = StreamOptions()
            stream_option_snd.mask_send = True
            stream_option_snd.unmask_receive = False
            self.stream = Stream(request_snd, stream_option_snd)
            print ("Audio data stream active...")
            msg_list = ["SET auth t=kiwi p=%s ipl=%s" % (password_, password_),
                        "SET mod=%s low_cut=%d high_cut=%d freq=%.3f" % (self.radio_mode.lower(), self.lc, self.hc, self.freq),
                        "SET compression=0", "SET ident_user=SuperSDR","SET OVERRIDE inactivity_timeout=1000",
                        "SET agc=%d hang=%d thresh=%d slope=%d decay=%d manGain=%d" % (self.on, self.hang, self.thresh, self.slope, self.decay, self.gain),
                        "SET AR OK in=%d out=%d" % (self.KIWI_RATE, self.AUDIO_RATE)]
            for msg in msg_list:
                self.stream.send_message(msg)
            while True:
                msg = self.stream.receive_message()
                if msg and "SND" == bytearray2str(msg[:3]):
                    break
                elif msg and "MSG audio_init" in bytearray2str(msg):
                    msg = bytearray2str(msg)
                    els = msg[4:].split()
                    if len(els) >= 2:
                        try:
                            self.KIWI_RATE = int(els[1].split("=")[1])
                        except (ValueError, IndexError) as e:
                            print(f"Error parsing KIWI_RATE from audio_init: {e}")
                            self.KIWI_RATE = 12000
                    if len(els) >= 3:
                        try:
                            self.KIWI_RATE_TRUE = float(els[2].split("=")[1])
                            self.delta_t = self.KIWI_RATE_TRUE - self.KIWI_RATE
                            self.SAMPLE_RATIO = self.AUDIO_RATE/self.KIWI_RATE
                        except (ValueError, IndexError) as e:
                            print(f"Error parsing KIWI_RATE_TRUE from audio_init: {e}")
        except:
            print ("Failed to connect to Kiwi audio stream")
            raise
        self.kiwi_filter = filtering(self.KIWI_RATE/2, self.AUDIO_RATE)
        gcd = np.gcd((self.KIWI_RATE),self.AUDIO_RATE)
        self.n_low, self.n_high = int(self.KIWI_RATE/gcd), int(self.AUDIO_RATE/gcd)
        self.n_tap = self.kiwi_filter.n_tap
        self.lowpass = self.kiwi_filter.lowpass
        self.old_buffer = np.zeros((self.n_tap-1))
        self.audio_rec = audio_recording(self)
        self.frames = []

    def run(self):
        while not self.terminate:
            try:
                msg = self.stream.receive_message()
            except:
                break
            
            if msg and bytearray2str(msg[0:3]) == "SND":
                flags,seq, = struct.unpack('<BI', buffer(msg[3:8]))
                smeter,    = struct.unpack('>H',  buffer(msg[8:10]))
                data       = msg[10:]
                self.rssi = 0.1*smeter - 127
                
                count = len(data) // 2
                samples = np.ndarray(count, dtype='>h', buffer=data).astype(np.float32)
                
                if self.KIWI_RATE != self.AUDIO_RATE:
                    samples = resample_poly(samples, self.AUDIO_RATE, self.KIWI_RATE)
                
                self.frames.append(samples)
                if len(self.frames) >= self.CHUNKS:
                    chunk = np.concatenate(self.frames)
                    self.frames = []
                    
                    if self.audio_rec.recording_flag:
                        self.audio_rec.audio_buffer.append(chunk.astype(np.int16).tobytes())

                    if self.CHANNELS == 2:
                        chunk_stereo = np.column_stack((chunk, chunk))
                        try:
                            self.audio_buffer.put(chunk_stereo.astype(np.int16).tobytes(), block=False)
                        except queue.Full:
                            try:
                                self.audio_buffer.get_nowait()
                                self.audio_buffer.put(chunk_stereo.astype(np.int16).tobytes(), block=False)
                            except:
                                pass
                    else:
                        try:
                            self.audio_buffer.put(chunk.astype(np.int16).tobytes(), block=False)
                        except queue.Full:
                             try:
                                self.audio_buffer.get_nowait()
                                self.audio_buffer.put(chunk.astype(np.int16).tobytes(), block=False)
                             except:
                                pass

    def play_buffer(self, outdata, frames, time, status):
        try:
            data = self.audio_buffer.get_nowait()
            audio_data = np.frombuffer(data, dtype=self.FORMAT)
            
            if self.CHANNELS == 2:
                audio_data = audio_data.reshape(-1, 2)
            
            if len(audio_data) <= len(outdata):
                outdata[:len(audio_data)] = audio_data
                outdata[len(audio_data):] = 0
            else:
                outdata[:] = audio_data[:len(outdata)]
        except queue.Empty:
            outdata[:] = 0

    def set_mode_freq_pb(self):
        self.stream.send_message("SET mod=%s low_cut=%d high_cut=%d freq=%.3f" % (self.radio_mode.lower(), self.lc, self.hc, self.freq))

    def set_agc(self, on, hang, thresh, slope, decay, gain):
        self.on, self.hang, self.thresh, self.slope, self.decay, self.gain = on, hang, thresh, slope, decay, gain
        self.stream.send_message("SET agc=%d hang=%d thresh=%d slope=%d decay=%d manGain=%d" % (self.on, self.hang, self.thresh, self.slope, self.decay, self.gain))

    def set_nb(self, on, thresh, gate):
        self.stream.send_message("SET nb=%d thresh=%d gate=%d" % (on, thresh, gate))

    def set_nr(self, on, log2n, sigma, gain):
        self.stream.send_message("SET nr=%d log2n=%d sigma=%d gain=%d" % (on, log2n, sigma, gain))

    def change_passband(self, delta_low, delta_high):
        self.lc = max(LOW_CUT_SSB, self.lc + delta_low)
        self.hc = min(HIGH_CUT_SSB, self.hc + delta_high)
        return self.lc, self.hc

class audio_recording:
    CHANNELS = 1
    def __init__(self, kiwi_snd):
        self.filename = ""
        self.audio_buffer = []
        self.kiwi_snd = kiwi_snd
        self.frames = []
        self.recording_flag = False

    def start(self):
        self.filename = "supersdr_%sUTC.wav" % datetime.utcnow().isoformat().split(".")[0].replace(":", "_")
        print("start recording")
        self.audio_buffer = []
        self.recording_flag = True

    def stop(self):
        print("stop recording")
        self.recording_flag = False
        self.save()

    def save(self):
        self.wave = wave.open(self.filename, "wb")
        self.wave.setnchannels(self.CHANNELS)
        self.wave.setsampwidth(2)  # two bytes per sample (int16)
        self.wave.setframerate(self.kiwi_snd.AUDIO_RATE)
        # process audio data here
        self.wave.writeframes(b"".join(self.audio_buffer))
        self.wave.close()
        self.recording = False

class filtering:
    def __init__(self, cutoff, fs):
        self.n_tap = int(10 * fs / 1000)
        self.nyq = fs / 2
        self.lowpass = np.zeros(self.n_tap)

        taps = np.linspace(-np.pi, np.pi, self.n_tap)
        for i in range(self.n_tap):
            val = 0.42 * (1 - np.cos(2 * np.pi * i / (self.n_tap - 1)))
            self.lowpass[i] = val

class kiwi_list:
    def __init__(self):
        self.entry_kiwi = None
        self.kiwi_list = []

        if os.path.exists(".kiwisdr_list"):
            try:
                with open(".kiwisdr_list") as f:
                    for line in f:
                        line = line.rstrip()
                        if line and not line.startswith("#"):
                            self.kiwi_list.append(line.split(":"))
            except:
                pass

class cat:
    CAT_MIN_FREQ = 100
    CAT_MAX_FREQ = 30000
    def __init__(self, radiohost_, radioport_):
        self.KNOWN_MODES = {"USB", "LSB", "CW", "AM"}
        self.radiohost, self.radioport = radiohost_, radioport_
        print ("RTX rigctld server: %s:%d" % (self.radiohost, self.radioport))
        self.socket = socket.socket()
        self.socket.settimeout(3.0)
        self.cat_ok = False
        try:
            self.socket.connect((self.radiohost, self.radioport))
            self.cat_ok = True
        except:
            self.cat_ok = False
            return
        
        if self.cat_ok:
            self.freq = self.get_freq()
            if not self.freq:
                self.cat_ok = False
                return
            self.radio_mode = self.get_mode()
            self.vfo = "A"
            self.reply = None
            self.cat_tx = False
        else:
            self.freq = None
            self.radio_mode = "USB"

    def send_msg(self, msg):
        self.socket.send((msg+"\n").encode())
        try:
            out = self.socket.recv(64).decode()
        except:
            out = ""
        if len(out)==0 or "RPRT -5" in out:
             self.cat_ok = False
             self.reply = None
        else:
            self.reply = out        

    def get_ptt(self):
        self.send_msg("\\get_ptt")
        if self.reply:
            try:
                self.cat_tx = True if self.reply=="1\n" else False
            except:
                self.cat_tx = False

    def set_freq(self, freq_):
        if freq_ >= self.CAT_MIN_FREQ and freq_ <= self.CAT_MAX_FREQ:
            self.send_msg(("\\set_freq %d" % (freq_*1000)))
            self.freq = freq_

    def set_mode(self, radio_mode_):
        self.send_msg(("\\set_mode %s 2400"%radio_mode_))
        if self.reply:
            self.radio_mode = radio_mode_

    def get_vfo(self):
        self.send_msg("\\get_vfo")
        if self.reply:
            try:
                self.vfo = "A" if "VFOA" in self.reply else "B"
            except:
                self.cat_ok = False

    def get_freq(self):
        self.get_vfo()
        self.send_msg("\\get_freq")
        if self.reply:
            try:
                self.freq = int(self.reply)/1000.
            except:
                self.cat_ok = False
        return self.freq

    def get_mode(self):
        self.send_msg("\\get_mode")
        if self.reply:
            self.radio_mode = self.reply.split("\n")[0]
            if self.radio_mode not in self.KNOWN_MODES:
                self.radio_mode = "USB"
            return self.radio_mode
        return "USB"

    def disconnect(self):
        try:
            self.socket.close()
            print("CAT radio disconnected.")
        except Exception as e:
            print(f"Error disconnecting CAT radio: {e}")

def start_audio_stream(kiwi_snd):
    def _get_std_input_dev():
        std_dev_id = None
        devices = sd.query_devices()
        for dev_id, device in enumerate(devices):
            if device["max_input_channels"] > 0 and "pulse" in device["name"]:
                std_dev_id = dev_id
        return std_dev_id

    rx_t = threading.Thread(target=kiwi_snd.run, daemon=True)
    rx_t.start()

    print("Filling audio buffer...")
    while kiwi_snd.audio_buffer.qsize() < kiwi_snd.FULL_BUFF_LEN and not kiwi_snd.terminate:
        pass

    if kiwi_snd.terminate:
        print("kiwi sound not started!")
        del kiwi_snd
        return (None, None)

    std_dev_id = _get_std_input_dev()
    kiwi_audio_stream = sd.OutputStream(blocksize = int(kiwi_snd.KIWI_SAMPLES_PER_FRAME*kiwi_snd.CHUNKS*kiwi_snd.SAMPLE_RATIO),
                        device=std_dev_id, dtype=kiwi_snd.FORMAT, latency="low", samplerate=kiwi_snd.AUDIO_RATE, channels=kiwi_snd.CHANNELS, callback = kiwi_snd.play_buffer)
    kiwi_audio_stream.start()

    return True, kiwi_audio_stream