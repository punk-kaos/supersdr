# supersdr_qt.py - Qt version of SuperSDR with pure backend (NO pygame)
import sys
import os
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QGridLayout, QWidget, QLabel, QStackedLayout, QFrame, QPushButton, QGroupBox, QSlider, QTabWidget, QButtonGroup, QLineEdit, QCheckBox, QComboBox
from PyQt5.QtCore import Qt, QSize, QTimer, QRect, QDateTime, QPoint, pyqtSignal, QSettings
from PyQt5.QtGui import QFont, QColor, QPainter, QPen, QPainterPath, QImage, QFontDatabase, QBrush, QFontMetrics

import numpy as np
import math
import threading
from collections import deque
from typing import Any, Dict

# Import pure backend (NO pygame)
import backend
import utils_supersdr

kiwi_waterfall = backend.kiwi_waterfall
kiwi_sound = backend.kiwi_sound
eibi_db = backend.eibi_db
kiwi_list = backend.kiwi_list
memory = backend.memory
beacons = backend.beacons
get_auto_mode = backend.get_auto_mode
from utils_supersdr import dxcluster
cat = backend.cat
LOW_CUT_SSB = backend.LOW_CUT_SSB
HIGH_CUT_SSB = backend.HIGH_CUT_SSB
LOW_CUT_CW = backend.LOW_CUT_CW
HIGH_CUT_CW = backend.HIGH_CUT_CW
HIGHLOW_CUT_AM = backend.HIGHLOW_CUT_AM
CW_PITCH = backend.CW_PITCH
VERSION = backend.VERSION
TENMHZ = backend.TENMHZ
HELP_MESSAGE_LIST = backend.HELP_MESSAGE_LIST

from optparse import OptionParser

default_kiwi_password = ""
default_kiwi_port = 8073
default_kiwi_server = "kiwisdr.local"

parser = OptionParser()
parser.add_option("-w", "--password", type=str,
                  help="KiwiSDR password", dest="kiwipassword", default=default_kiwi_password)
parser.add_option("-s", "--kiwiserver", type=str,
                  help="KiwiSDR server name", dest="kiwiserver", default=default_kiwi_server)
parser.add_option("-p", "--kiwiport", type=int,
                  help="port number", dest="kiwiport", default=default_kiwi_port)
parser.add_option("-z", "--zoom", type=int,
                  help="zoom factor", dest="zoom", default=8)
parser.add_option("-f", "--freq", type=float,
                   help="center frequency in kHz", dest="freq", default=None)
parser.add_option("-c", "--callsign", type=str,
                  help="DXCluster callsign", dest="callsign", default="")
parser.add_option("--rigctld-host", type=str,
                  help="rigctld host (default: localhost)", dest="rigctld_host", default="localhost")
parser.add_option("--rigctld-port", type=int,
                  help="rigctld port (default: 4532)", dest="rigctld_port", default=4532)

options = vars(parser.parse_args()[0])

kiwi_host = options['kiwiserver']
kiwi_port = options['kiwiport']
kiwi_password = options['kiwipassword']
zoom = options['zoom']
freq = options['freq']
callsign = options['callsign']

if not freq:
    freq = 14200

# Display constants
DISPLAY_WIDTH = 1024
TOPBAR_HEIGHT = 23
BOTTOMBAR_HEIGHT = 20
BOTTOM_DECK_HEIGHT = 220  # Increased from 160 for better CAT tab visibility
TUNEBAR_HEIGHT = 23
_calculated_display_height = DISPLAY_WIDTH // 2 
WF_HEIGHT = _calculated_display_height * 75 // 100 - BOTTOMBAR_HEIGHT - TUNEBAR_HEIGHT  # Increased waterfall
SPECTRUM_HEIGHT = _calculated_display_height * 15 // 100 - TOPBAR_HEIGHT  # Reduced from 40% to 15%
DISPLAY_HEIGHT = WF_HEIGHT + SPECTRUM_HEIGHT + TOPBAR_HEIGHT + BOTTOMBAR_HEIGHT + TUNEBAR_HEIGHT + BOTTOM_DECK_HEIGHT

# Define common Qt colors
QGREY = QColor(200,200,200)
QWHITE = QColor(255,255,255)
QBLACK = QColor(0,0,0)
QD_GREY = QColor(50,50,50)
QD_RED = QColor(200,0,0)
QD_BLUE = QColor(0,0,200)
QD_GREEN = QColor(0,120,0)
QRED = QColor(255,0,0)
QBLUE = QColor(0,0,255)
QGREEN = QColor(0,255,0)
QYELLOW = QColor(200,180,0)
QORANGE = QColor(255,140,0)

def generate_cutesdr_colormap():
    colormap = []
    for i in range(255):
        if i < 43:
            r, g, b = 0, 0, int(255 * (i) / 43)
        elif i < 87:
            r, g, b = 0, int(255 * (i - 43) / 43), 255
        elif i < 120:
            r, g, b = 0, 255, int(255 - (255 * (i - 87) / 32))
        elif i < 154:
            r, g, b = int(255 * (i - 120) / 33), 255, 0
        elif i < 217:
            r, g, b = 255, int(255 - (255 * (i - 154) / 62)), 0
        else:
            r, g, b = 255, 0, int(128 * (i - 217) / 38)
        colormap.append(QColor(r, g, b))
    return colormap

class SpectrumWidget(QWidget):
    def __init__(self, parent=None, colormap=None, spectrum_height=SPECTRUM_HEIGHT, display_width=DISPLAY_WIDTH):
        super().__init__(parent)
        self.setMinimumHeight(50)  # Reduced from 100 to allow smaller spectrum
        # Remove fixed height to allow resizing
        # self.setMaximumHeight(spectrum_height)
        self.colormap = colormap
        self.spectrum_data = None
        self.wf_auto_scaling = True
        self.wf_min_db = -120
        self.wf_max_db = -60
        # Don't store fixed dimensions - use dynamic width()/height()
        self.filled = False
        self.col = QYELLOW
        self.D_GREEN = QD_GREEN
        
    def update_spectrum_data(self, spectrum_data, wf_auto_scaling=True, wf_min_db=-120, wf_max_db=-60, filled=False, col=QYELLOW):
        self.spectrum_data = spectrum_data
        self.wf_auto_scaling = wf_auto_scaling
        self.wf_min_db = wf_min_db
        self.wf_max_db = wf_max_db
        self.filled = filled
        self.col = col
        self.update()

    def paintEvent(self, event):
        if self.spectrum_data is None:
            return

        # Use dynamic dimensions
        width = self.width()
        height = self.height()
        
        if width <= 0 or height <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QD_GREY)

        data_len = len(self.spectrum_data)
        if data_len == 0:
            return

        if self.filled:
            path = QPainterPath()
            path.moveTo(0, height)
            for i in range(data_len):
                # Map data index to screen x coordinate
                x = int(i * width / data_len)
                v_scaled = self.spectrum_data[i]
                y = height - 1 - int(v_scaled / 255.0 * height)
                path.lineTo(x, y)
            path.lineTo(width, height)
            path.closeSubpath()
            
            painter.fillPath(path, self.col)
        else:
            pen = QPen(self.col, 1)
            painter.setPen(pen)
            
            # Draw line connecting spectrum points
            for i in range(data_len - 1):
                x1 = int(i * width / data_len)
                x2 = int((i + 1) * width / data_len)
                y1 = height - 1 - int(self.spectrum_data[i] / 255.0 * height)
                y2 = height - 1 - int(self.spectrum_data[i+1] / 255.0 * height)
                painter.drawLine(x1, y1, x2, y2)
        
        if not self.wf_auto_scaling:
            wf_dyn_range = self.wf_max_db - self.wf_min_db
            if wf_dyn_range > 0:
                min_wf_10 = int(self.wf_min_db / 10) * 10
                max_wf_10 = int(self.wf_max_db / 10) * 10
                
                pen = QPen(self.D_GREEN, 1, Qt.DotLine)
                painter.setPen(pen)

                for db_val in range(min_wf_10, max_wf_10 + 1, 10):
                    if self.wf_min_db <= db_val <= self.wf_max_db:
                        y_div = height - 1 - int((db_val - self.wf_min_db) / wf_dyn_range * height)
                        if 0 <= y_div < height:
                            painter.drawLine(0, y_div, width, y_div)

class WaterfallWidget(QWidget):
    def __init__(self, parent=None, colormap=None, wf_height=WF_HEIGHT, display_width=DISPLAY_WIDTH):
        super().__init__(parent)
        self.setMinimumHeight(100)  # Allow resizing
        # Remove fixed height to allow resizing
        # self.setMinimumHeight(wf_height)
        # self.setMaximumHeight(wf_height)
        self.colormap = colormap
        
        # Start with initial dimensions
        self.waterfall_image = QImage(display_width, wf_height, QImage.Format_RGB32)
        self.waterfall_image.fill(QBLACK)

    def resizeEvent(self, event):
        """Handle widget resize by recreating waterfall image"""
        super().resizeEvent(event)
        
        new_width = self.width()
        new_height = self.height()
        
        if new_width > 0 and new_height > 0:
            # Create new image with new size
            new_image = QImage(new_width, new_height, QImage.Format_RGB32)
            new_image.fill(QBLACK)
            
            # Scale and copy old image if it exists
            if not self.waterfall_image.isNull():
                painter = QPainter(new_image)
                # Scale old image to fit new dimensions
                scaled_old = self.waterfall_image.scaled(new_width, new_height, 
                                                         Qt.IgnoreAspectRatio, 
                                                         Qt.FastTransformation)
                painter.drawImage(0, 0, scaled_old)
                painter.end()
            
            self.waterfall_image = new_image

    def update_waterfall_data(self, new_wf_line):
        if new_wf_line is None:
            return
        
        width = self.width()
        height = self.height()
        
        if width <= 0 or height <= 0:
            return
        
        # Ensure image matches current widget size
        if (self.waterfall_image.isNull() or 
            self.waterfall_image.width() != width or 
            self.waterfall_image.height() != height):
            self.waterfall_image = QImage(width, height, QImage.Format_RGB32)
            self.waterfall_image.fill(QBLACK)
            return  # Skip this frame after resize
        
        if self.colormap is None:
            return

        # Shift image down by 1 pixel
        temp_image = QImage(width, height, QImage.Format_RGB32)
        painter = QPainter(temp_image)
        # Copy old image shifted down by 1 pixel
        painter.drawImage(0, 1, self.waterfall_image, 0, 0, width, height - 1)
        painter.end()
        self.waterfall_image = temp_image

        # Draw new line at top, scaling it to current width
        line_len = len(new_wf_line)
        for x in range(width):
            # Map screen x coordinate to data index
            data_idx = int(x * line_len / width) if line_len > 0 else 0
            data_idx = min(data_idx, line_len - 1)
            
            value = new_wf_line[data_idx]
            color_index = int(np.clip(value, 0, 254))
            color = self.colormap[color_index]
            self.waterfall_image.setPixelColor(x, 0, color)
        
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self.waterfall_image.isNull():
            painter.drawImage(0, 0, self.waterfall_image)

class TextOverlayWidget(QWidget):
    def __init__(self, parent=None, fonts=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

        self.fonts = fonts if fonts is not None else {}
        self.text_elements = {}

    def update_text_elements(self, new_elements):
        self.text_elements.update(new_elements)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        for text_id, element in self.text_elements.items():
            text = element.get('text', '')
            pos = element.get('pos', (0, 0))
            font_name = element.get('font_name', 'smallfont')
            color = element.get('color', QWHITE)
            bgcolor = element.get('bgcolor', None)
            rotation = element.get('rotation', 0)

            font = self.fonts.get(font_name)
            if font:
                painter.setFont(font)
            else:
                painter.setFont(QFont("Arial", 10))

            painter.setPen(color)

            font_metrics = painter.fontMetrics()
            text_rect = font_metrics.boundingRect(QRect(0, 0, 1, 1), Qt.AlignLeft, text) 

            if bgcolor:
                bg_rect = QRect(pos[0], pos[1], text_rect.width(), text_rect.height())
                painter.fillRect(bg_rect, bgcolor)

            painter.save()
            painter.translate(pos[0] + text_rect.width()/2, pos[1] + text_rect.height()/2)
            painter.rotate(rotation)
            painter.drawText(QRect(int(-text_rect.width()/2), int(-text_rect.height()/2), text_rect.width(), text_rect.height()), Qt.AlignCenter, text)
            painter.restore()

class SMeterWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.s_meter_radius = 60
        self.s_meter_border = 2
        self.rssi_smooth = -127
        self.rssi_smooth_slow = -127
        self.rssi_hist = deque(maxlen=10)
        self.agc_threshold = -60
        self.agc_decay = 5000
        self.thresh = -60
        self.decay = self.agc_decay

    def update_s_meter(self, rssi, rssi_slow, thresh, decay):
        self.rssi_smooth = rssi
        self.rssi_slow = rssi_slow
        self.thresh = thresh
        self.decay = decay
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))

        smeter_x = 10 + self.s_meter_radius + self.s_meter_border
        smeter_center_x = smeter_x + self.s_meter_radius
        smeter_center_y = 30 + self.s_meter_radius + self.s_meter_border

        rssi_v0 = -20 + 127
        angle_offset = 0.2
        
        alpha_rssi = -math.radians(self.rssi_smooth + 127) + angle_offset
        if self.rssi_smooth < -127:
            alpha_rssi = -math.pi/2
        alpha_agc = -math.radians(self.thresh + 127) + angle_offset
        if self.rssi_smooth > 0:
            alpha_agc = -math.pi/2
        
        if alpha_rssi > alpha_agc:
            alpha_rssi = alpha_agc

        def coords_from_angle(angle, radius):
            x = radius * math.cos(angle)
            y = radius * math.sin(angle)
            return smeter_center_x + x, smeter_center_y - y

        agc_meter_x, agc_meter_y = coords_from_angle(alpha_agc, self.s_meter_radius * 0.7)

        if alpha_rssi < alpha_agc:
            s_meter_x, s_meter_y = coords_from_angle(alpha_rssi, self.s_meter_radius * 0.95)
            painter.setPen(QPen(QYELLOW, 3))
            painter.setBrush(QBrush(QYELLOW, Qt.NoBrush))
            painter.drawLine(int(s_meter_x), int(s_meter_y), int(agc_meter_x), int(agc_meter_y))

        angle_list = np.linspace(angle_offset, math.pi - angle_offset, 9)
        text_list = ["1", "3", "5", "7", "9", "+12", "+24", "+36", "+48"]
        
        painter.setPen(QPen(QWHITE, 1))
        painter.setFont(QFont("Arial", 8))
        
        for i, angle in enumerate(angle_list):
            x, y = coords_from_angle(angle, self.s_meter_radius)
            painter.drawPoint(int(x), int(y))
            
            if angle < math.pi:
                text_angle = angle - math.pi/2
            else:
                text_angle = angle - 3*math.pi/2
            
            painter.save()
            painter.translate(int(x), int(y))
            painter.rotate(math.degrees(text_angle))
            painter.drawText(text_list[i], -10, -10)
            painter.restore()

        painter.setPen(QPen(QBLACK, 2))
        painter.setBrush(QBrush(QBLACK, Qt.NoBrush))
        painter.drawEllipse(int(smeter_center_x - self.s_meter_radius - self.s_meter_border),
                        int(smeter_center_y - self.s_meter_radius - self.s_meter_border),
                        int(self.s_meter_radius * 2 + self.s_meter_border * 2),
                        int(self.s_meter_radius * 2 + self.s_meter_border * 2))

        painter.setPen(QPen(QBLUE, 2))
        painter.drawArc(int(smeter_center_x - self.s_meter_radius - self.s_meter_border),
                      int(smeter_center_y - self.s_meter_radius - self.s_meter_border),
                      int(self.s_meter_radius * 2 + self.s_meter_border * 2),
                      int(math.degrees(-math.pi/2 + angle_offset)), int(math.degrees(math.pi/2 + angle_offset)))

        painter.setPen(QPen(QRED, 1))
        painter.drawLine(int(smeter_center_x - self.s_meter_radius - self.s_meter_border),
                      int(smeter_center_y - self.s_meter_radius - self.s_meter_border),
                      int(smeter_center_x + self.s_meter_radius + self.s_meter_border + self.s_meter_border),
                      int(smeter_center_y + self.s_meter_radius + self.s_meter_border + self.s_meter_border))

class TuneOverlayWidget(QWidget):
    tune_clicked = pyqtSignal(float)
    wf_dragged = pyqtSignal(float)

    def __init__(self, parent=None, fonts=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.fonts = fonts if fonts is not None else {}
        self.overlay_data = {}
        self.mouse_pos = QPoint(-1, -1)
        self.click_drag_flag = False
        self.start_drag_x = -1
        
    def _freq_from_x(self, x_pos):
        bins2pixel_ratio = self.overlay_data.get('bins2pixel_ratio', 1.0)
        start_f_khz = self.overlay_data.get('start_f_khz', 0.0)
        span_khz = self.overlay_data.get('span_khz', 100.0)
        wf_bins = self.overlay_data.get('wf_bins', 1024)
        
        if bins2pixel_ratio == 0:
            bins2pixel_ratio = DISPLAY_WIDTH / wf_bins
            
        bins = x_pos / bins2pixel_ratio
        return (span_khz / wf_bins) * bins + start_f_khz

    def update_overlay_data(self, data):
        self.overlay_data.update(data)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QColor(0,0,0,0))

        center_freq_bin = self.overlay_data.get('center_freq_bin', 0)
        bins2pixel_ratio = self.overlay_data.get('bins2pixel_ratio', 1)
        wf_y = self.overlay_data.get('wf_y')
        wf_height = self.overlay_data.get('wf_height')
        tunebar_y = self.overlay_data.get('tunebar_y', 0)
        tunebar_height = self.overlay_data.get('tunebar_height', 0)
        spectrum_y = self.overlay_data.get('spectrum_y', 0)

        if wf_y is not None and wf_height is not None:
            painter.setPen(QPen(QRED, 4))
            painter.drawLine(int(center_freq_bin * bins2pixel_ratio), wf_y, int(center_freq_bin * bins2pixel_ratio), wf_y + 6)
            
            rx_freq = self.overlay_data.get('rx_freq')
            start_f_khz = self.overlay_data.get('start_f_khz')
            span_khz = self.overlay_data.get('span_khz')
            wf_bins = self.overlay_data.get('wf_bins')
            
            if rx_freq is not None and start_f_khz is not None and span_khz and wf_bins:
                rx_bin = (float(rx_freq) - float(start_f_khz)) / (float(span_khz) / float(wf_bins))
                rx_x = int(rx_bin * bins2pixel_ratio)
                
                # Draw filter passband rectangle over waterfall FIRST (so red line is on top)
                filter_lc = self.overlay_data.get('filter_lc')
                filter_hc = self.overlay_data.get('filter_hc')
                
                if filter_lc is not None and filter_hc is not None and span_khz > 0:
                    # Convert filter cutoff frequencies (Hz) to screen coordinates
                    # filter_lc and filter_hc are offsets in Hz from rx_freq
                    # Convert to kHz offset
                    lc_offset_khz = filter_lc / 1000.0
                    hc_offset_khz = filter_hc / 1000.0
                    
                    # Calculate absolute frequencies
                    lc_freq = rx_freq + lc_offset_khz
                    hc_freq = rx_freq + hc_offset_khz
                    
                    # Convert to screen x coordinates
                    lc_bin = (lc_freq - start_f_khz) / (span_khz / wf_bins)
                    hc_bin = (hc_freq - start_f_khz) / (span_khz / wf_bins)
                    
                    lc_x = lc_bin * bins2pixel_ratio
                    hc_x = hc_bin * bins2pixel_ratio
                    
                    # Clip to screen boundaries
                    lc_x = max(0, min(lc_x, DISPLAY_WIDTH))
                    hc_x = max(0, min(hc_x, DISPLAY_WIDTH))
                    
                    # Draw semi-transparent rectangle showing the passband
                    if hc_x > lc_x:  # Only draw if there's visible width
                        # Choose color based on mode
                        radio_mode = self.overlay_data.get('radio_mode', 'USB')
                        if radio_mode in ['AM', 'NFM']:
                            filter_color = QColor(0, 150, 255, 60)  # Blue, 60/255 opacity (~23%)
                        elif radio_mode == 'CW':
                            filter_color = QColor(255, 200, 0, 60)  # Yellow, 60/255 opacity
                        else:  # USB, LSB
                            filter_color = QColor(0, 255, 0, 60)    # Green, 60/255 opacity
                        
                        painter.fillRect(int(lc_x), wf_y, int(hc_x - lc_x), wf_height, filter_color)
                
                # Draw red RX center line AFTER filter (so it's on top and always visible)
                if 0 <= rx_x <= DISPLAY_WIDTH:
                    # Draw through waterfall
                    painter.setPen(QPen(QRED, 2))
                    painter.drawLine(rx_x, wf_y, rx_x, wf_y + wf_height)
                    # Draw on tune bar
                    painter.setPen(QPen(QRED, 1))
                    painter.drawLine(rx_x, int(tunebar_y + tunebar_height // 2), rx_x, int(tunebar_y + tunebar_height))

        if self.rect().contains(self.mouse_pos) and wf_y is not None and wf_height is not None and tunebar_y is not None and tunebar_height is not None:
            if (wf_y <= self.mouse_pos.y() <= wf_y + wf_height) or (tunebar_y <= self.mouse_pos.y() <= tunebar_y + tunebar_height):
                pen_color = QRED if (self.mouse_pos.y() >= wf_y) else QGREEN
                painter.setPen(QPen(pen_color, 1))
                painter.drawLine(self.mouse_pos.x(), tunebar_y, self.mouse_pos.x(), wf_y + wf_height)

        subdiv_list = self.overlay_data.get('subdiv_list', [])
        div_list = self.overlay_data.get('div_list', [])

        if tunebar_y is not None and tunebar_height is not None:
            for x_bin in subdiv_list:
                painter.setPen(QPen(QWHITE, 1))
                painter.drawLine(int(x_bin * bins2pixel_ratio), tunebar_y + tunebar_height, int(x_bin * bins2pixel_ratio), tunebar_y + tunebar_height - 6)
            for x_bin in div_list:
                painter.setPen(QPen(QYELLOW, 3))
                painter.drawLine(int(x_bin * bins2pixel_ratio), tunebar_y + tunebar_height, int(x_bin * bins2pixel_ratio), tunebar_y + tunebar_height - 11)

            # Draw memory label lines
            memory_labels = self.overlay_data.get('memory_labels', [])
            for mem_label in memory_labels:
                x_pos = mem_label.get('x_pos', 0)
                y_pos = mem_label.get('y_pos', 0)
                y_offset = mem_label.get('y_offset', 0)
                painter.setPen(QPen(QGREEN, 1))
                painter.drawLine(int(x_pos), tunebar_y, int(x_pos), tunebar_y - 20 + y_offset)

        if self.click_drag_flag and self.start_drag_x != -1 and spectrum_y is not None:
            painter.setPen(QPen(QRED, 4))
            y_pos = spectrum_y + 10
            painter.drawLine(self.start_drag_x, y_pos, self.mouse_pos.x(), y_pos)

    def mouseMoveEvent(self, event):
        self.mouse_pos = event.pos()
        if self.click_drag_flag:
            self.update()
        else:
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            wf_y = self.overlay_data.get('wf_y', 0)
            wf_height = self.overlay_data.get('wf_height', 0)
            spectrum_y = self.overlay_data.get('spectrum_y', 0)
            tunebar_y = self.overlay_data.get('tunebar_y', 0)
            tunebar_height = self.overlay_data.get('tunebar_height', 0)
            
            y = event.pos().y()
            x = event.pos().x()
            
            if (wf_y <= y <= wf_y + wf_height) or (tunebar_y <= y <= tunebar_y + tunebar_height):
                freq = self._freq_from_x(x)
                if freq is not None:
                    self.tune_clicked.emit(freq)
            
            elif spectrum_y <= y <= tunebar_y:
                self.click_drag_flag = True
                self.start_drag_x = x
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.click_drag_flag:
                bins2pixel_ratio = self.overlay_data.get('bins2pixel_ratio', 1)
                delta_x = (event.pos().x() - self.start_drag_x) / bins2pixel_ratio
                span_khz = self.overlay_data.get('span_khz', 100)
                wf_bins = self.overlay_data.get('wf_bins', 1024)
                delta_freq = (span_khz / wf_bins) * delta_x
                self.wf_dragged.emit(delta_freq)
                
            self.click_drag_flag = False
            self.start_drag_x = -1
            self.update()


class ControlDeck(QWidget):
    freq_entered = pyqtSignal(float)
    band_clicked = pyqtSignal(float)
    nr_toggled = pyqtSignal(bool)
    nb_toggled = pyqtSignal(bool)
    agc_toggled = pyqtSignal(bool)
    att_toggled = pyqtSignal(bool)
    
    def __init__(self, parent=None, initial_freq=14200.0):
        super().__init__(parent)
        self.setFixedHeight(BOTTOM_DECK_HEIGHT)
        self.setStyleSheet("""
            QWidget { background-color: #1a1a1a; color: #e0e0e0; font-family: 'Terminus (TTF)', monospace; }
            QGroupBox { border: 1px solid #444; border-radius: 4px; margin-top: 20px; font-weight: bold; color: #888; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }
            QPushButton { background-color: #333; border: 1px solid #555; border-radius: 2px; padding: 4px; color: #ccc; }
            QPushButton:hover { background-color: #444; border-color: #666; }
            QPushButton:checked { background-color: #d32f2f; color: white; border-color: #ff5252; }
            QPushButton:pressed { background-color: #b71c1c; }
            QSlider::groove:horizontal { border: 1px solid #333; height: 4px; background: #222; margin: 2px 0; }
            QSlider::handle:horizontal { background: #888; border: 1px solid #555; width: 12px; height: 12px; margin: -6px 0; border-radius: 6px; }
            QTabWidget::pane { border: 1px solid #444; background: #222; }
            QTabBar::tab { background: #2a2a2a; color: #888; border: 1px solid #444; padding: 4px 12px; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }
            QTabBar::tab:selected { background: #333; color: #fff; border-bottom: 1px solid #333; }
            QComboBox { background-color: #333; color: #ccc; border: 1px solid #555; border-radius: 2px; padding: 2px; min-height: 20px; }
            QComboBox::drop-down { border: 0px; }
            QComboBox QAbstractItemView { background-color: #333; color: #ccc; selection-background-color: #555; }
        """)
        
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(15)

        self.vfo_frame = QFrame()
        self.vfo_frame.setStyleSheet("QFrame { background-color: #000; border: 2px solid #333; border-radius: 6px; }")
        vfo_layout = QVBoxLayout(self.vfo_frame)
        vfo_layout.setContentsMargins(10, 5, 10, 5)
        
        self.freq_label = QLineEdit(f"{initial_freq:.3f}")
        self.freq_label.setAlignment(Qt.AlignCenter)
        self.freq_label.setStyleSheet("""
            QLineEdit { 
                font-size: 36px; 
                font-weight: bold; 
                color: #00ff00; 
                background-color: black;
                border: none; 
                font-family: 'Terminus (TTF)'; 
            }
        """)
        self.freq_label.returnPressed.connect(self._on_freq_entered)

        self.band_combo = QComboBox()
        self.bands_list = [
            ("Band Select", None),
            ("160m", 1800), ("80m", 3500), ("60m", 5330), ("40m", 7000),
            ("30m", 10100), ("20m", 14000), ("17m", 18068), ("15m", 21000),
            ("12m", 24890), ("10m", 28000)
        ]
        for name, freq in self.bands_list:
            self.band_combo.addItem(name, freq)
        self.band_combo.currentIndexChanged.connect(self._on_band_combo_changed)
        
        self.smeter_label = QLabel("S-Meter: S9+10dB")
        self.smeter_label.setAlignment(Qt.AlignCenter)
        self.smeter_label.setStyleSheet("font-size: 14px; color: #ffaa00; border: none;")
        
        vfo_layout.addWidget(self.freq_label)
        vfo_layout.addWidget(self.band_combo)
        vfo_layout.addWidget(self.smeter_label)
        main_layout.addWidget(self.vfo_frame, 2)

        mode_group = QGroupBox("MODE")
        mode_layout = QGridLayout(mode_group)
        mode_layout.setContentsMargins(5, 15, 5, 5)
        mode_layout.setSpacing(5)
        
        self.modes = ["USB", "LSB", "CW", "AM", "NFM", "DIG"]
        self.mode_buttons = {}
        positions = [(0,0), (0,1), (0,2), (1,0), (1,1), (1,2)]
        
        self.mode_group_exclusive = QButtonGroup(self)
        for mode, pos in zip(self.modes, positions):
            btn = QPushButton(mode)
            btn.setCheckable(True)
            if mode == "USB":
                btn.setChecked(True)
            if mode == "DIG":  # Only DIG is not supported, NFM is now supported
                btn.setEnabled(False)
                btn.setToolTip("Not supported yet")
            mode_layout.addWidget(btn, *pos)
            self.mode_buttons[mode] = btn
            self.mode_group_exclusive.addButton(btn)
            
        main_layout.addWidget(mode_group, 1)

        tune_group = QGroupBox("TUNING")
        tune_layout = QVBoxLayout(tune_group)
        tune_layout.setContentsMargins(10, 15, 10, 5)
        
        zoom_hbox = QHBoxLayout()
        zoom_hbox.addWidget(QLabel("ZOOM"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(0, 14)
        self.zoom_slider.setValue(8)
        zoom_hbox.addWidget(self.zoom_slider)
        tune_layout.addLayout(zoom_hbox)
        
        bw_hbox = QHBoxLayout()
        bw_hbox.addWidget(QLabel("WIDTH"))
        self.bw_slider = QSlider(Qt.Horizontal)
        self.bw_slider.setRange(100, 12000)
        self.bw_slider.setValue(2400)
        bw_hbox.addWidget(self.bw_slider)
        tune_layout.addLayout(bw_hbox)
        
        main_layout.addWidget(tune_group, 2)

        self.tabs = QTabWidget()

        audio_tab = QWidget()
        audio_layout = QVBoxLayout(audio_tab)
        audio_layout.setContentsMargins(5, 5, 5, 5)

        vol_hbox = QHBoxLayout()
        vol_hbox.addWidget(QLabel("VOL"))
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 150)
        self.vol_slider.setValue(100)
        vol_hbox.addWidget(self.vol_slider)
        audio_layout.addLayout(vol_hbox)

        self.mute_btn = QPushButton("MUTE")
        self.mute_btn.setCheckable(True)
        audio_layout.addWidget(self.mute_btn)

        self.tabs.addTab(audio_tab, "AUDIO")

        band_tab = QWidget()
        band_layout = QGridLayout(band_tab)
        band_layout.setContentsMargins(5, 5, 5, 5)
        band_layout.setSpacing(2)
        bands = [
            ("160m", 1800), ("80m", 3500), ("60m", 5330), ("40m", 7000),
            ("30m", 10100), ("20m", 14000), ("17m", 18068), ("15m", 21000),
            ("12m", 24890), ("10m", 28000), ("Air", 118000), ("FM", 88000)
        ]
        self.band_buttons = {}
        for i, (name, f) in enumerate(bands):
            btn = QPushButton(name)
            btn.setMaximumHeight(24)
            btn.clicked.connect(lambda ch, freq=f: self.band_clicked.emit(float(freq)))
            band_layout.addWidget(btn, i // 4, i % 4)
        self.tabs.addTab(band_tab, "BANDS")

        sig_tab = QWidget()
        sig_layout = QGridLayout(sig_tab)
        self.nr_btn = QPushButton("NR")
        self.nr_btn.setCheckable(True)
        self.nr_btn.clicked.connect(lambda ch: self.nr_toggled.emit(ch))
        
        self.nb_btn = QPushButton("NB")
        self.nb_btn.setCheckable(True)
        self.nb_btn.clicked.connect(lambda ch: self.nb_toggled.emit(ch))
        
        self.agc_btn = QPushButton("AGC")
        self.agc_btn.setCheckable(True)
        self.agc_btn.setChecked(True)
        self.agc_btn.clicked.connect(lambda ch: self.agc_toggled.emit(ch))
        
        self.att_btn = QPushButton("ATT")
        self.att_btn.setCheckable(True)
        self.att_btn.clicked.connect(lambda ch: self.att_toggled.emit(ch))

        sig_layout.addWidget(self.nr_btn, 0, 0)
        sig_layout.addWidget(self.nb_btn, 0, 1)
        sig_layout.addWidget(self.agc_btn, 1, 0)
        sig_layout.addWidget(self.att_btn, 1, 1)
        self.tabs.addTab(sig_tab, "SIGNAL")

        cat_tab = QWidget()
        cat_layout = QVBoxLayout(cat_tab)
        cat_layout.setContentsMargins(10, 10, 10, 10)
        cat_layout.setSpacing(10)
        
        cat_layout.addWidget(QLabel("CAT Control"))

        host_layout = QHBoxLayout()
        host_label = QLabel("Host:")
        host_label.setFixedWidth(50)
        host_layout.addWidget(host_label)
        self.cat_host_input = QLineEdit("localhost")
        self.cat_host_input.setMinimumHeight(30)
        self.cat_host_input.setStyleSheet("background-color: #333; color: #fff; padding: 5px; border: 1px solid #555; font-size: 12px;")
        host_layout.addWidget(self.cat_host_input)
        cat_layout.addLayout(host_layout)

        port_layout = QHBoxLayout()
        port_label = QLabel("Port:")
        port_label.setFixedWidth(50)
        port_layout.addWidget(port_label)
        self.cat_port_input = QLineEdit("4532")
        self.cat_port_input.setMinimumHeight(30)
        self.cat_port_input.setStyleSheet("background-color: #333; color: #fff; padding: 5px; border: 1px solid #555; font-size: 12px;")
        port_layout.addWidget(self.cat_port_input)
        cat_layout.addLayout(port_layout)

        self.cat_status_label = QLabel("Status: Disconnected")
        self.cat_status_label.setMinimumHeight(25)
        self.cat_status_label.setStyleSheet("color: #ff5252; font-weight: bold; font-size: 12px;")
        cat_layout.addWidget(self.cat_status_label)

        self.cat_sync_radio_to_kiwi_freq_cb = QCheckBox("Sync Freq (Radio to Kiwi)")
        self.cat_sync_radio_to_kiwi_freq_cb.setMinimumHeight(25)
        self.cat_sync_radio_to_kiwi_freq_cb.setStyleSheet("font-size: 11px;")
        cat_layout.addWidget(self.cat_sync_radio_to_kiwi_freq_cb)

        self.cat_sync_kiwi_to_radio_freq_cb = QCheckBox("Sync Freq (Kiwi to Radio)")
        self.cat_sync_kiwi_to_radio_freq_cb.setMinimumHeight(25)
        self.cat_sync_kiwi_to_radio_freq_cb.setStyleSheet("font-size: 11px;")
        cat_layout.addWidget(self.cat_sync_kiwi_to_radio_freq_cb)
        
        self.cat_sync_radio_to_kiwi_mode_cb = QCheckBox("Sync Mode (Radio to Kiwi)")
        self.cat_sync_radio_to_kiwi_mode_cb.setMinimumHeight(25)
        self.cat_sync_radio_to_kiwi_mode_cb.setStyleSheet("font-size: 11px;")
        cat_layout.addWidget(self.cat_sync_radio_to_kiwi_mode_cb)

        self.cat_sync_kiwi_to_radio_mode_cb = QCheckBox("Sync Mode (Kiwi to Radio)")
        self.cat_sync_kiwi_to_radio_mode_cb.setMinimumHeight(25)
        self.cat_sync_kiwi_to_radio_mode_cb.setStyleSheet("font-size: 11px;")
        cat_layout.addWidget(self.cat_sync_kiwi_to_radio_mode_cb)
        
        # Load saved checkbox states from QSettings
        settings = QSettings("SuperSDR", "Qt")
        self.cat_sync_radio_to_kiwi_freq_cb.setChecked(settings.value("cat_sync_radio_to_kiwi_freq", False, type=bool))
        self.cat_sync_kiwi_to_radio_freq_cb.setChecked(settings.value("cat_sync_kiwi_to_radio_freq", False, type=bool))
        self.cat_sync_radio_to_kiwi_mode_cb.setChecked(settings.value("cat_sync_radio_to_kiwi_mode", False, type=bool))
        self.cat_sync_kiwi_to_radio_mode_cb.setChecked(settings.value("cat_sync_kiwi_to_radio_mode", False, type=bool))

        self.cat_connect_btn = QPushButton("Connect")
        self.cat_connect_btn.setMinimumHeight(35)
        self.cat_connect_btn.setStyleSheet("font-size: 12px; font-weight: bold; padding: 5px;")
        cat_layout.addWidget(self.cat_connect_btn)
        
        cat_layout.addStretch()
        self.tabs.addTab(cat_tab, "CAT")

        sys_tab = QWidget()
        sys_layout = QVBoxLayout(sys_tab)
        sys_layout.addWidget(QPushButton("CONNECT"))
        sys_layout.addWidget(QPushButton("SETTINGS"))
        self.tabs.addTab(sys_tab, "SYSTEM")

        main_layout.addWidget(self.tabs, 2)
    
    def update_cat_status_ui(self, is_active: bool):
        if is_active:
            self.cat_status_label.setText("Status: Connected")
            self.cat_status_label.setStyleSheet("color: #00ff00; font-weight: bold;") # Green for connected
            self.cat_connect_btn.setText("Disconnect")
            self.cat_host_input.setEnabled(False)
            self.cat_port_input.setEnabled(False)
            self.cat_sync_radio_to_kiwi_freq_cb.setEnabled(True)
            self.cat_sync_kiwi_to_radio_freq_cb.setEnabled(True)
            self.cat_sync_radio_to_kiwi_mode_cb.setEnabled(True)
            self.cat_sync_kiwi_to_radio_mode_cb.setEnabled(True)
        else:
            self.cat_status_label.setText("Status: Disconnected")
            self.cat_status_label.setStyleSheet("color: #ff5252; font-weight: bold;") # Red for disconnected
            self.cat_connect_btn.setText("Connect")
            self.cat_host_input.setEnabled(True)
            self.cat_port_input.setEnabled(True)
            self.cat_sync_radio_to_kiwi_freq_cb.setEnabled(False)
            self.cat_sync_kiwi_to_radio_freq_cb.setEnabled(False)
            self.cat_sync_radio_to_kiwi_mode_cb.setEnabled(False)
            self.cat_sync_kiwi_to_radio_mode_cb.setEnabled(False)

    def _on_freq_entered(self):
        try:
            freq_str = self.freq_label.text().replace(',', '')
            freq_khz = float(freq_str)
            self.freq_entered.emit(freq_khz)
            self.freq_label.clearFocus()
        except ValueError:
            pass

    def _on_band_combo_changed(self, index):
        data = self.band_combo.itemData(index)
        if data is not None:
            self.band_clicked.emit(float(data))



class SuperSDRMainWindow(QMainWindow):
    cat_status_changed_signal = pyqtSignal(bool)
    def __init__(self, options, callsign):
        super().__init__(None)
        self.setWindowTitle("SuperSDR - Qt Edition (Pure Backend)")
        self.setGeometry(100, 100, DISPLAY_WIDTH, DISPLAY_HEIGHT)
        self.setMinimumSize(800, 600)  # Enable resizing with minimum size

        self.kiwi_host = options['kiwiserver']
        self.kiwi_port = options['kiwiport']
        self.kiwi_password = options['kiwipassword']
        self.kiwi_wf_zoom = options['zoom']
        self.current_freq = options['freq'] if options['freq'] else 14200.0
        self.callsign = callsign
        self.rigctld_host = options['rigctld_host']
        self.rigctld_port = options['rigctld_port']

        font_db = QFontDatabase()
        current_dir = os.path.dirname(os.path.abspath(__file__))

        
        terminus_ttf_path = os.path.join(current_dir, "TerminusTTF-4.49.1.ttf")
        if font_db.addApplicationFont(terminus_ttf_path) == -1:
            print(f"Warning: Failed to load font {terminus_ttf_path}")

        terminus_bold_ttf_path = os.path.join(current_dir, "TerminusTTF-Bold-4.49.1.ttf")
        if font_db.addApplicationFont(terminus_bold_ttf_path) == -1:
            print(f"Warning: Failed to load font {terminus_bold_ttf_path}")
        
        self.fonts = {
            "nanofont": QFont("Terminus (TTF)", 10),
            "microfont": QFont("Terminus (TTF)", 12),
            "smallfont": QFont("Terminus (TTF)", 16, QFont.Bold),
            "midfont": QFont("Terminus (TTF)", 16),
            "bigfont": QFont("Terminus (TTF)", 20, QFont.Bold),
            "hugefont": QFont("Terminus (TTF)", 35)
        }

        central_container_widget = QWidget()
        self.setCentralWidget(central_container_widget)
        main_vbox = QVBoxLayout(central_container_widget)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        main_vbox.setSpacing(0)

        # Remove fixed size to allow resizing
        top_interaction_widget = QWidget()
        # top_interaction_widget.setFixedSize(DISPLAY_WIDTH, interaction_height)
        
        self.stacked_layout = QStackedLayout(top_interaction_widget)
        self.stacked_layout.setStackingMode(QStackedLayout.StackAll)

        base_layer_widget = QWidget()
        base_layout = QVBoxLayout(base_layer_widget)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(0)

        self.top_bar = QLabel("Top Bar (Placeholder)")
        self.top_bar.setFixedHeight(TOPBAR_HEIGHT)
        self.top_bar.setStyleSheet("background-color: #333; color: white;")
        self.top_bar.setAlignment(Qt.AlignCenter)
        base_layout.addWidget(self.top_bar, 0)  # stretch = 0

        self.shared_colormap = generate_cutesdr_colormap()

        self.spectrum_widget = SpectrumWidget(self, colormap=self.shared_colormap)
        # Remove fixed height to allow resizing
        # self.spectrum_widget.setFixedHeight(SPECTRUM_HEIGHT)
        self.spectrum_widget.setStyleSheet("background-color: #555;")
        base_layout.addWidget(self.spectrum_widget, 1)  # stretch = 1 (reduced for smaller spectrum)

        self.tune_bar = QLabel("Tune Bar (Placeholder)")
        self.tune_bar.setFixedHeight(TUNEBAR_HEIGHT)
        self.tune_bar.setStyleSheet("background-color: #444; color: white;")
        self.tune_bar.setAlignment(Qt.AlignCenter)
        base_layout.addWidget(self.tune_bar, 0)  # stretch = 0

        self.waterfall_widget = WaterfallWidget(self, colormap=self.shared_colormap)
        # Remove fixed height to allow resizing
        # self.waterfall_widget.setFixedHeight(WF_HEIGHT)
        self.waterfall_widget.setStyleSheet("background-color: #222;")
        base_layout.addWidget(self.waterfall_widget, 5)  # stretch = 5 (increased for larger waterfall)

        self.stacked_layout.addWidget(base_layer_widget)

        self.text_overlay_widget = TextOverlayWidget(top_interaction_widget, fonts=self.fonts)
        # Remove fixed geometry to allow resizing
        # self.text_overlay_widget.setGeometry(0, 0, DISPLAY_WIDTH, interaction_height)
        self.stacked_layout.addWidget(self.text_overlay_widget)

        self.tune_overlay_widget = TuneOverlayWidget(top_interaction_widget, fonts=self.fonts)
        # Remove fixed geometry to allow resizing
        # self.tune_overlay_widget.setGeometry(0, 0, DISPLAY_WIDTH, interaction_height)
        self.stacked_layout.addWidget(self.tune_overlay_widget)
        
        self.tune_overlay_widget.raise_()
        
        main_vbox.addWidget(top_interaction_widget)

        self.control_deck = ControlDeck(self, initial_freq=self.current_freq)
        self.cat_status_changed_signal.connect(self.control_deck.update_cat_status_ui)
        main_vbox.addWidget(self.control_deck)
        
        self._deck_updating = False
        self.muted = False
        self.last_volume = self.control_deck.vol_slider.value()

        self.statusBar().setStyleSheet("background-color: #333; color: white;")
        self.statusBar().setFixedHeight(BOTTOMBAR_HEIGHT)
        self.statusBar().showMessage("Ready")

        self.waterfall_timer = QTimer(self)
        self.waterfall_timer.timeout.connect(self._update_waterfall_real)
        self.waterfall_timer.start(50)

        self.TOPBAR_Y = 0
        self.SPECTRUM_Y = TOPBAR_HEIGHT
        self.TUNEBAR_Y = self.SPECTRUM_Y + SPECTRUM_HEIGHT
        self.WF_Y = self.TUNEBAR_Y + TUNEBAR_HEIGHT
        self.BOTTOMBAR_Y = self.WF_Y + WF_HEIGHT + BOTTOM_DECK_HEIGHT
        
        self.kiwi_wf = None
        self.kiwi_snd = None
        self.beacon_project = None

        self.cat_radio = None
        self.wf_cat_link_flag = False
        self.wf_snd_link_flag = True
        self.cat_snd_link_flag = False
        self.auto_mode = True
        self.s_meter_show_flag = False

        self.delta_low, self.delta_high = 0., 0.
        
        # Use instance attributes from options for initial setup
        self.current_mode = get_auto_mode(self.current_freq) if get_auto_mode else "USB"
        self.audio_buffer_len = 50
        self.dual_rx_active = False
        self.cat_active = False
        self.recording_active = False
        self.dxcluster_active = False
        self.adc_overflow = False
        self.show_mem_flag = False
        self.show_dxcluster_flag = False
        self.dxclust = None
        self.show_eibi_flag = False
        self.kiwi_wf_zoom = zoom
        self.kiwi_wf_span_khz = 30000 / (2**zoom)
        self.kiwi_wf_start_f_khz = freq - self.kiwi_wf_span_khz / 2
        self.kiwi_wf_end_f_khz = freq + self.kiwi_wf_span_khz / 2
        self.kiwi_wf_wf_bins = DISPLAY_WIDTH
        self.kiwi_wf_bins2pixel_ratio = DISPLAY_WIDTH / self.kiwi_wf_wf_bins
        self.kiwi_wf_subdiv_list = [DISPLAY_WIDTH // 4, DISPLAY_WIDTH // 2, DISPLAY_WIDTH * 3 // 4]
        self.kiwi_wf_div_list = [DISPLAY_WIDTH // 2]

        self.init_backend()
        self._bind_control_deck()

        self.bar_update_timer = QTimer(self)
        self.bar_update_timer.timeout.connect(self.update_bar_info)
        self.bar_update_timer.start(100)

        self.overlay_update_timer = QTimer(self)
        self.overlay_update_timer.timeout.connect(self.update_overlays)
        self.overlay_update_timer.start(50)

        self.s_meter_timer = QTimer(self)
        self.s_meter_timer.timeout.connect(self.update_s_meter_data)
        self.s_meter_timer.start(50)
        
        # CAT polling timer for radio-to-kiwi sync
        self.cat_poll_timer = QTimer(self)
        self.cat_poll_timer.timeout.connect(self.poll_cat_radio)
        self.cat_poll_timer.start(500)  # Poll every 500ms

        # Only initialize if backend connection succeeded
        kiwi_wf = self.kiwi_wf
        kiwi_snd = self.kiwi_snd
        if kiwi_wf is not None and kiwi_snd is not None:
            kiwi_wf.set_freq_zoom(freq, zoom)
            kiwi_snd.freq = freq
            kiwi_snd.radio_mode = self.current_mode
            self.delta_low, self.delta_high = 0.0, 0.0
            kiwi_snd.change_passband(self.delta_low, self.delta_high)
            self.lc = getattr(kiwi_snd, "lc", LOW_CUT_SSB)
            self.hc = getattr(kiwi_snd, "hc", HIGH_CUT_SSB)
            kiwi_snd.set_mode_freq_pb()
        else:
            # Test mode - use simulated data
            test_spectrum_data = np.random.randint(0, 255, size=DISPLAY_WIDTH)
            self.spectrum_widget.update_spectrum_data(test_spectrum_data, wf_auto_scaling=False, wf_min_db=-100, wf_max_db=-40, filled=True, col=QRED)
            self.waterfall_timer.timeout.disconnect()
            self.waterfall_timer.timeout.connect(self._update_waterfall_test)

    def init_backend(self):
        try:
            self.eibi = eibi_db()
            self.disp_mock = type('obj', (object,), {'DISPLAY_WIDTH': DISPLAY_WIDTH, 'WF_HEIGHT': WF_HEIGHT, 'SPECTRUM_HEIGHT': SPECTRUM_HEIGHT, 'TOPBAR_HEIGHT': TOPBAR_HEIGHT, 'TUNEBAR_HEIGHT': TUNEBAR_HEIGHT, 'BOTTOMBAR_HEIGHT': BOTTOMBAR_HEIGHT, 'SPECTRUM_FILLED': False})()

            print(f"Connecting to KiwiSDR at {self.kiwi_host}:{self.kiwi_port}")
            self.kiwi_wf = kiwi_waterfall(self.kiwi_host, self.kiwi_port, kiwi_password, self.kiwi_wf_zoom, self.current_freq, self.eibi, self.disp_mock)
            self.kiwi_wf_thread = threading.Thread(target=self.kiwi_wf.run, daemon=True)
            self.kiwi_wf_thread.start()
            print("KiwiSDR waterfall thread started - NO pygame!")

            self.kiwi_snd = kiwi_sound(self.current_freq, "USB", 30, 3000, kiwi_password, self.kiwi_wf, 10)
            print("KiwiSDR audio initialized - NO pygame!")
            backend.start_audio_stream(self.kiwi_snd)
            print("Audio stream started.")

            self.kiwi_memory = memory()
            self.beacon_project = beacons()

            if self.callsign:
                try:
                    self.dxclust = dxcluster(self.callsign)
                except Exception as e:
                    print(f"Failed to initialize DXCluster: {e}")
                    self.dxclust = None
            else:
                self.dxclust = None
            
            # Instantiate CAT radio
            self.cat_radio = None
            if self.rigctld_host and self.rigctld_port:
                try:
                    # The cat class constructor attempts to connect
                    self.cat_radio = cat(self.rigctld_host, self.rigctld_port)
                    if not self.cat_radio.cat_ok:
                        print(f"Failed to connect to rigctld at {self.rigctld_host}:{self.rigctld_port}")
                        self.cat_radio = None
                except Exception as e:
                    print(f"Error initializing CAT radio: {e}")
                    self.cat_radio = None
            else:
                print("rigctld host or port not provided. CAT control disabled.")
            
            self.cat_active = True if self.cat_radio else False
            self.cat_status_changed_signal.emit(self.cat_active) # Emit signal for initial UI update


            self.waterfall_timer.timeout.disconnect()
            self.waterfall_timer.timeout.connect(self._update_waterfall_real)

            print("SUCCESS: Pure backend with KiwiSDR connection!")
        except Exception as e:
            print(f"Failed to initialize backend: {e}")
            print("Running in TEST MODE with simulated data")

            # Stop the waterfall thread if it's still running
            if hasattr(self, 'kiwi_wf_thread') and self.kiwi_wf is not None:
                if hasattr(self.kiwi_wf, 'terminate'):
                    self.kiwi_wf.terminate = True

            self.kiwi_wf = None
            self.kiwi_snd = None

    def _update_waterfall_test(self):
        new_line_data = np.random.randint(0, 255, size=DISPLAY_WIDTH)
        self.waterfall_widget.update_waterfall_data(new_line_data)

    def _update_waterfall_real(self):
        if self.kiwi_wf and hasattr(self.kiwi_wf, 'wf_data'):
            if self.kiwi_wf.wf_data is not None and len(self.kiwi_wf.wf_data) > 0:
                latest_line = self.kiwi_wf.wf_data[0]
                if len(latest_line) != DISPLAY_WIDTH:
                    latest_line = np.interp(
                        np.linspace(0, len(latest_line) - 1, DISPLAY_WIDTH),
                        np.arange(len(latest_line)),
                        latest_line,
                    ).astype(np.uint8)
                self.waterfall_widget.update_waterfall_data(latest_line)

            if hasattr(self.kiwi_wf, 'spectrum') and self.kiwi_wf.spectrum is not None:
                    spectrum_data = self.kiwi_wf.spectrum.astype(np.uint8)
                    if len(spectrum_data) > 0:
                        if len(spectrum_data) != DISPLAY_WIDTH:
                            spectrum_data = np.interp(
                                np.linspace(0, len(spectrum_data) - 1, DISPLAY_WIDTH),
                                np.arange(len(spectrum_data)),
                                spectrum_data,
                            ).astype(np.uint8)
                        self.spectrum_widget.update_spectrum_data(
                            spectrum_data,
                            wf_auto_scaling=self.kiwi_wf.wf_auto_scaling,
                            wf_min_db=int(self.kiwi_wf.wf_min_db),
                            wf_max_db=int(self.kiwi_wf.wf_max_db),
                            filled=False,
                            col=QYELLOW,
                        )

    def _bind_control_deck(self):
        if not hasattr(self, "control_deck"):
            return

        for mode, btn in self.control_deck.mode_buttons.items():
            btn.clicked.connect(lambda _checked=False, m=mode: self._set_mode_from_ui(m))

        self.control_deck.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        self.control_deck.bw_slider.valueChanged.connect(self._on_bandwidth_changed)
        self.control_deck.vol_slider.valueChanged.connect(self._on_volume_changed)
        self.control_deck.mute_btn.clicked.connect(self._on_mute_clicked)
        self.control_deck.freq_entered.connect(self._on_tune_clicked)
        self.control_deck.band_clicked.connect(self._on_band_clicked)
        self.control_deck.nr_toggled.connect(self._on_nr_toggled)
        self.control_deck.nb_toggled.connect(self._on_nb_toggled)
        self.control_deck.agc_toggled.connect(self._on_agc_toggled)
        self.control_deck.att_toggled.connect(self._on_att_toggled)
        self.control_deck.cat_connect_btn.clicked.connect(self._on_cat_connect_toggle)
        self.tune_overlay_widget.tune_clicked.connect(self._on_tune_clicked)
        self.tune_overlay_widget.wf_dragged.connect(self._on_wf_dragged)
        
        # Connect CAT checkbox signals to save their state
        self.control_deck.cat_sync_radio_to_kiwi_freq_cb.toggled.connect(self._save_cat_checkbox_state)
        self.control_deck.cat_sync_kiwi_to_radio_freq_cb.toggled.connect(self._save_cat_checkbox_state)
        self.control_deck.cat_sync_radio_to_kiwi_mode_cb.toggled.connect(self._save_cat_checkbox_state)
        self.control_deck.cat_sync_kiwi_to_radio_mode_cb.toggled.connect(self._save_cat_checkbox_state)

    def _save_cat_checkbox_state(self):
        """Save CAT checkbox states to QSettings"""
        settings = QSettings("SuperSDR", "Qt")
        settings.setValue("cat_sync_radio_to_kiwi_freq", self.control_deck.cat_sync_radio_to_kiwi_freq_cb.isChecked())
        settings.setValue("cat_sync_kiwi_to_radio_freq", self.control_deck.cat_sync_kiwi_to_radio_freq_cb.isChecked())
        settings.setValue("cat_sync_radio_to_kiwi_mode", self.control_deck.cat_sync_radio_to_kiwi_mode_cb.isChecked())
        settings.setValue("cat_sync_kiwi_to_radio_mode", self.control_deck.cat_sync_kiwi_to_radio_mode_cb.isChecked())
        settings.sync()  # Ensure immediate write to disk

    def _set_slider_value(self, slider, value):
        slider.blockSignals(True)
        slider.setValue(int(value))
        slider.blockSignals(False)

    def _set_mode_from_ui(self, mode):
        self.current_mode = mode
        if self.kiwi_snd:
            self.kiwi_snd.radio_mode = mode
            
            # Set default bandwidths for modes
            if mode == "CW":
                default_bw = 500
            elif mode in ["AM", "NFM"]:
                default_bw = 6000
            else:  # SSB
                default_bw = 2700
                
            self.control_deck.bw_slider.blockSignals(True)
            self.control_deck.bw_slider.setValue(default_bw)
            self.control_deck.bw_slider.blockSignals(False)
            
            self._apply_bandwidth(default_bw)

        if self.kiwi_wf:
            self.kiwi_wf.radio_mode = mode
        
        # Sync mode to CAT radio if enabled
        if self.cat_active and self.cat_radio and self.control_deck.cat_sync_kiwi_to_radio_mode_cb.isChecked():
            try:
                self.cat_radio.set_mode(mode)
                print(f"Synced mode to CAT radio: {mode}")
            except Exception as e:
                print(f"Error syncing mode to CAT radio: {e}")

    def _apply_bandwidth(self, width):
        if not self.kiwi_snd:
            return

        width = max(100, int(width))
        mode = self.kiwi_snd.radio_mode

        if mode == "USB":
            self.lc = LOW_CUT_SSB
            self.hc = self.lc + width
        elif mode == "LSB":
            self.hc = -LOW_CUT_SSB
            self.lc = self.hc - width
        elif mode == "CW":
            center = CW_PITCH * 1000
            self.lc = int(center - width / 2)
            self.hc = int(center + width / 2)
        elif mode == "AM" or mode == "NFM":
            self.lc = int(-width / 2)
            self.hc = int(width / 2)
        else:
            # Fallback for DIG or others
            self.lc = 0
            self.hc = width

        self.kiwi_snd.lc = int(self.lc)
        self.kiwi_snd.hc = int(self.hc)
        self.kiwi_snd.set_mode_freq_pb()

    def _on_zoom_changed(self, value):
        self.kiwi_wf_zoom = value
        if self.kiwi_wf:
            base_freq = self.kiwi_snd.freq if self.kiwi_snd else self.current_freq
            self.kiwi_wf.set_freq_zoom(base_freq, value)

    def _on_bandwidth_changed(self, value):
        self._apply_bandwidth(value)

    def _on_volume_changed(self, value):
        if self.kiwi_snd:
            self.kiwi_snd.volume = value

        if value > 0:
            self.last_volume = value
            self.muted = False
        else:
            self.muted = True

    def _on_mute_clicked(self):
        if not self.kiwi_snd:
            return

        if self.muted:
            restore_volume = self.last_volume if self.last_volume > 0 else 100
            self._set_slider_value(self.control_deck.vol_slider, restore_volume)
        else:
            if self.kiwi_snd.volume > 0:
                self.last_volume = self.kiwi_snd.volume
            self._set_slider_value(self.control_deck.vol_slider, 0)

    def _on_tune_clicked(self, freq):
        self.current_freq = freq
        if self.kiwi_snd:
            tuned_freq = freq
            if self.kiwi_snd.radio_mode == "CW":
                tuned_freq -= CW_PITCH
            self.kiwi_snd.freq = tuned_freq
            self.kiwi_snd.set_mode_freq_pb()
        
        # Sync to CAT radio if enabled
        if self.cat_active and self.cat_radio and self.control_deck.cat_sync_kiwi_to_radio_freq_cb.isChecked():
            try:
                self.cat_radio.set_freq(freq)
            except Exception as e:
                print(f"Error syncing frequency to CAT radio: {e}")
        
        if self.wf_snd_link_flag:
            if self.kiwi_wf:
                self.kiwi_wf.set_freq_zoom(freq, self.kiwi_wf.zoom)
        self.update_bar_info()

    def _on_wf_dragged(self, delta_freq):
        if self.kiwi_wf:
            new_wf_freq = self.kiwi_wf.freq - delta_freq
            self.kiwi_wf.set_freq_zoom(new_wf_freq, self.kiwi_wf.zoom)
            
            if self.wf_snd_link_flag and self.kiwi_snd:
                self.kiwi_snd.freq = self.kiwi_wf.freq
                self.kiwi_snd.set_mode_freq_pb()
        self.update_bar_info()

    def _on_band_clicked(self, freq):
        if self.kiwi_snd:
            self.kiwi_snd.freq = float(freq)
            self.kiwi_snd.radio_mode = get_auto_mode(self.kiwi_snd.freq) if get_auto_mode else "USB"
            self.kiwi_snd.set_mode_freq_pb()
        
        # Sync to CAT radio if enabled
        if self.cat_active and self.cat_radio and self.control_deck.cat_sync_kiwi_to_radio_freq_cb.isChecked():
            try:
                self.cat_radio.set_freq(float(freq))
            except Exception as e:
                print(f"Error syncing frequency to CAT radio: {e}")
        
        if self.kiwi_wf:
            self.kiwi_wf.set_freq_zoom(float(freq), self.kiwi_wf.zoom)
        self.current_freq = float(freq)
        self.update_bar_info()

    def _on_nr_toggled(self, checked):
        if self.kiwi_snd:
            self.kiwi_snd.set_nr(1 if checked else 0, 0, 0, 100)

    def _on_nb_toggled(self, checked):
        if self.kiwi_snd:
            self.kiwi_snd.set_nb(1 if checked else 0, 50, 10)

    def _on_agc_toggled(self, checked):
        if self.kiwi_snd:
            on = 1 if checked else 0
            self.kiwi_snd.set_agc(on, self.kiwi_snd.hang, self.kiwi_snd.thresh, self.kiwi_snd.slope, self.kiwi_snd.decay, self.kiwi_snd.gain)

    def _on_att_toggled(self, checked):
        if self.kiwi_wf and self.kiwi_wf.wf_stream:
            self.kiwi_wf.wf_stream.send_message("SET att=%d" % (10 if checked else 0))

    def _on_cat_connect_toggle(self):
        if self.cat_active:
            # Disconnect
            if self.cat_radio:
                # Assuming a disconnect method exists or should be added to utils_supersdr.cat
                if hasattr(self.cat_radio, 'disconnect'):
                    self.cat_radio.disconnect() 
                else:
                    print("Warning: cat.disconnect() method not found.")
            self.cat_active = False
            self.cat_radio = None
            print("CAT disconnected.")
        else:
            # Connect
            rigctld_host = self.control_deck.cat_host_input.text()
            try:
                rigctld_port = int(self.control_deck.cat_port_input.text()) # Assuming valid int input
            except ValueError:
                print("Invalid rigctld port. Please enter a number.")
                return
            
            try:
                self.cat_radio = cat(rigctld_host, rigctld_port)
                if self.cat_radio.cat_ok:
                    self.cat_active = True
                    print(f"CAT connected to {rigctld_host}:{rigctld_port}")
                else:
                    self.cat_active = False
                    self.cat_radio = None
                    print(f"Failed to connect to rigctld at {rigctld_host}:{rigctld_port}")
            except Exception as e:
                self.cat_active = False
                self.cat_radio = None
                print(f"Error connecting to CAT: {e}")
        
        self.cat_status_changed_signal.emit(self.cat_active)

    def update_bar_info(self):
        # Check if connections are still alive
        connection_status = "Connected"
        if self.kiwi_wf and hasattr(self.kiwi_wf, 'terminate') and self.kiwi_wf.terminate:
            connection_status = "⚠ Waterfall Disconnected"
        if self.kiwi_snd and hasattr(self.kiwi_snd, 'terminate') and self.kiwi_snd.terminate:
            connection_status = "⚠ Audio Disconnected"
        
        if self.kiwi_snd:
            current_freq = self.kiwi_snd.freq
            current_mode = self.kiwi_snd.radio_mode
            filter_lc = self.lc if hasattr(self, 'lc') else LOW_CUT_SSB
            filter_hc = self.hc if hasattr(self, 'hc') else HIGH_CUT_SSB
        else:
            current_freq = self.current_freq
            current_mode = self.current_mode
            filter_lc = LOW_CUT_SSB
            filter_hc = HIGH_CUT_SSB

        top_bar_text = f"UTC: {QDateTime.currentDateTimeUtc().toString('hh:mm:ss')} " \
                       f"RX: {current_freq:.3f}kHz {current_mode} " \
                       f"FILT: {filter_hc - filter_lc}Hz " \
                       f"AUTO: {'ON' if self.auto_mode else 'OFF'} " \
                       f"[{connection_status}]"
        self.top_bar.setText(top_bar_text)

        tune_bar_text = f"WF: {current_freq:.1f}kHz"
        self.tune_bar.setText(tune_bar_text)

        status_bar_text = f"Kiwi: {self.kiwi_host}:{self.kiwi_port} | " \
                          f"Buf: {self.audio_buffer_len} | " \
                          f"DRX: {'ON' if self.dual_rx_active else 'OFF'} | " \
                          f"CAT: {'ON' if self.cat_active else 'OFF'} | " \
                          f"REC: {'ON' if self.recording_active else 'OFF'} | " \
                          f"DX: {'ON' if self.dxcluster_active else 'OFF'} | " \
                          f"OVF: {'YES' if self.adc_overflow else 'NO'}" \
                          f"CENTER: {'ON' if self.wf_snd_link_flag else 'OFF'} | " \
                          f"SYNC: {'ON' if self.cat_snd_link_flag else 'OFF'} | " \
                          f"AUTO: {'ON' if self.auto_mode else 'OFF'}"
        self.statusBar().showMessage(status_bar_text)

        if hasattr(self, 'control_deck'):
            if not self.control_deck.freq_label.hasFocus():
                freq_val = float(current_freq)
                self.control_deck.freq_label.setText(f"{freq_val:.3f}")
            
            for mode, btn in self.control_deck.mode_buttons.items():
                btn.blockSignals(True)
                btn.setChecked(mode == current_mode)
                btn.blockSignals(False)
            
            self.control_deck.zoom_slider.blockSignals(True)
            self.control_deck.zoom_slider.setValue(int(self.kiwi_wf_zoom))
            self.control_deck.zoom_slider.blockSignals(False)
            
            if self.kiwi_snd:
                self.control_deck.vol_slider.blockSignals(True)
                self.control_deck.vol_slider.setValue(int(self.kiwi_snd.volume))
                self.control_deck.vol_slider.blockSignals(False)
                
                self.control_deck.mute_btn.blockSignals(True)
                self.control_deck.mute_btn.setChecked(self.kiwi_snd.volume == 0)
                self.control_deck.mute_btn.blockSignals(False)

    def update_overlays(self):
        wf = self.kiwi_wf
        if wf:
            center_freq_bin = wf.WF_BINS / 2
            bins2pixel_ratio = getattr(wf, 'BINS2PIXEL_RATIO', DISPLAY_WIDTH / wf.WF_BINS)
            subdiv_list = getattr(wf, 'subdiv_list', [])
            div_list = getattr(wf, 'div_list', [])
            start_f_khz = wf.start_f_khz
            span_khz = wf.span_khz
            wf_bins = wf.WF_BINS
        else:
            center_freq_bin = self.kiwi_wf_wf_bins / 2
            bins2pixel_ratio = self.kiwi_wf_bins2pixel_ratio
            subdiv_list = self.kiwi_wf_subdiv_list
            div_list = self.kiwi_wf_div_list
            start_f_khz = self.kiwi_wf_start_f_khz
            span_khz = self.kiwi_wf_span_khz
            wf_bins = self.kiwi_wf_wf_bins

        rx_freq = self.kiwi_snd.freq if self.kiwi_snd else self.current_freq
        radio_mode = self.kiwi_snd.radio_mode if self.kiwi_snd else self.current_mode
        
        # Get filter cutoff frequencies for passband visualization
        filter_lc = self.lc if hasattr(self, 'lc') else LOW_CUT_SSB
        filter_hc = self.hc if hasattr(self, 'hc') else HIGH_CUT_SSB

        tune_overlay_data: dict[str, Any] = {
            'center_freq_bin': center_freq_bin,
            'bins2pixel_ratio': bins2pixel_ratio,
            'wf_y': self.WF_Y,
            'wf_height': WF_HEIGHT,
            'tunebar_y': self.TUNEBAR_Y,
            'tunebar_height': TUNEBAR_HEIGHT,
            'subdiv_list': subdiv_list,
            'div_list': div_list,
            'mouse_pos': self.tune_overlay_widget.mouse_pos,
            'click_drag_flag': self.tune_overlay_widget.click_drag_flag,
            'start_drag_x': self.tune_overlay_widget.start_drag_x,
            'spectrum_y': self.SPECTRUM_Y,
            'start_f_khz': start_f_khz,
            'span_khz': span_khz,
            'wf_bins': wf_bins,
            'rx_freq': rx_freq,
            'radio_mode': radio_mode,
            'filter_lc': filter_lc,
            'filter_hc': filter_hc,
            'memory_labels': []
        }

        memory_labels_to_add: list[dict[str, Any]] = []
        if self.show_mem_flag and self.kiwi_wf and hasattr(self, 'kiwi_memory'):
            kiwi_memory = getattr(self, 'kiwi_memory')
            y_offset = 0
            old_fbin = -100
            sorted_freq_list = sorted([(i, m[0]) for i, m in enumerate(kiwi_memory.mem_list)], key=lambda x: x[1])

            for i, f_khz in sorted_freq_list:
                f_bin = int((f_khz - start_f_khz) * bins2pixel_ratio)
                x_pos = float(f_bin)

                font = self.fonts.get("smallfont", QFont("Arial", 10))
                font_metrics = QFontMetrics(font)
                text_width = font_metrics.width(str(i))

                if x_pos > text_width / 2 and x_pos < DISPLAY_WIDTH - 10:
                    if f_bin - old_fbin <= text_width / 2 + 5:
                        y_offset -= 16
                    else:
                        y_offset = 0
                    old_fbin = f_bin

                    memory_labels_to_add.append({
                        'x_pos': x_pos,
                        'y_pos': float(self.TUNEBAR_Y - 20),
                        'y_offset': float(y_offset)
                    })

        tune_overlay_data['memory_labels'] = memory_labels_to_add
        self.tune_overlay_widget.update_overlay_data(tune_overlay_data)

        dynamic_text_elements: dict[str, Any] = {
            "freq_label": {"text": f"{rx_freq:.3f} kHz", "pos": (50, 50), "font_name": "hugefont", "color": QYELLOW},
            "mode_label": {"text": f"{radio_mode}", "pos": (50, 100), "font_name": "bigfont", "color": QGREEN},
        }

        if self.show_dxcluster_flag and self.dxclust and hasattr(self.dxclust, 'visible_stations') and wf and wf.zoom > 3:
            visible_stations = getattr(self.dxclust, 'visible_stations', [])
            y_offset = 0
            old_fbin = -100
            for spot_id in visible_stations:
                try:
                    f_khz_float = float(self.dxclust.spot_dict[spot_id][1])
                    f_bin = int((f_khz_float - start_f_khz) * bins2pixel_ratio)
                    call = self.dxclust.spot_dict[spot_id][0]
                    font = self.fonts.get("smallfont", QFont("Arial", 10))
                    text_width = QFontMetrics(font).width(call)

                    if 0 < f_bin < DISPLAY_WIDTH:
                        if f_bin - old_fbin <= text_width / 2 + 5:
                            y_offset += 14
                        else:
                            y_offset = 0
                        old_fbin = f_bin

                        dynamic_text_elements[f"dx_{spot_id}"] = {
                            "text": call,
                            "pos": (int(f_bin - text_width / 2), int(self.WF_Y + 20 + (y_offset % int(WF_HEIGHT / 2)))),
                            "font_name": "smallfont", "color": QWHITE, "bgcolor": QColor(20, 20, 20), "rotation": 0
                        }
                except: pass

        if self.show_eibi_flag and hasattr(self, 'eibi') and wf and wf.zoom > 6:
            from datetime import datetime
            now_time = datetime.utcnow().hour + datetime.utcnow().minute / 60
            y_offset = 0
            old_fbin = -100
            for f_str in sorted(set(self.eibi.visible_stations), key=float):
                if f_str not in self.eibi.station_dict: continue
                for record in self.eibi.station_dict[f_str]:
                    try:
                        h1, m1 = int(record[0][:2]), int(record[0][2:4])
                        h2, m2 = int(record[0][5:7]), int(record[0][7:9])
                        if not (h1 + m1/60 <= now_time <= h2 + m2/60): continue
                        f_bin = int((float(f_str) - start_f_khz) * bins2pixel_ratio)
                        name = record[3]
                        text_width = QFontMetrics(self.fonts.get("smallfont", QFont("Arial", 10))).width(name)
                        if 0 < f_bin < DISPLAY_WIDTH:
                            if f_bin - old_fbin <= text_width / 2 + 5: y_offset += 16
                            else: y_offset = 0
                            old_fbin = f_bin
                            dynamic_text_elements[f"eibi_{f_str}_{name}"] = {
                                "text": name, "pos": (int(f_bin - text_width / 2), int(self.WF_Y + 20 + y_offset)),
                                "font_name": "smallfont", "color": QWHITE, "bgcolor": QColor(20, 20, 20), "rotation": 0
                            }
                    except: pass

        if hasattr(self, 'beacon_project') and self.beacon_project and wf and wf.zoom > 8:
            y_offset, old_fbin = 0, -100
            for b in self.beacon_project.freq_dict:
                if math.fabs(wf.freq - self.beacon_project.freq_dict[b]) < 100:
                    f_khz = float(self.beacon_project.freq_dict[b])
                    f_bin = int((f_khz - start_f_khz) * bins2pixel_ratio)
                    name = self.beacon_project.beacons_dict[b]
                    text_width = QFontMetrics(self.fonts.get("midfont", QFont("Arial", 12))).width(name)
                    if 0 < f_bin < DISPLAY_WIDTH:
                        dynamic_text_elements[f"beacon_{b}"] = {
                            "text": name, "pos": (int(f_bin - text_width / 2), int((self.SPECTRUM_Y + self.TUNEBAR_Y) / 2)),
                            "font_name": "midfont", "color": QGREEN, "bgcolor": QColor(20, 20, 20), "rotation": 0
                        }

        self.text_overlay_widget.update_text_elements(dynamic_text_elements)



    def update_s_meter_data(self):
        if not self.kiwi_snd or not self.s_meter_show_flag:
            return

        try:
            rssi = self.kiwi_snd.rssi
            self.s_meter_widget.rssi_hist.append(rssi)

            rssi_last = self.s_meter_widget.rssi_hist[-1]
            rssi_smooth = self.s_meter_widget.rssi_smooth
            rssi_v0 = -20 + 127

            if math.fabs(rssi_last) > math.fabs(rssi_smooth):
                t = math.log(rssi_v0/(rssi_smooth+135)) if (rssi_smooth+135) > 0 else 0
                rssi_smooth += -rssi_v0/(self.kiwi_snd.decay/(1000/(2*20))) * math.exp(-t)
            else:
                rssi_smooth += min((rssi_last - rssi_smooth)/5, 3)

            rssi_smooth_slow = max(self.s_meter_widget.rssi_hist)

            self.s_meter_widget.update_s_meter(rssi_smooth, rssi_smooth_slow,
                                               self.kiwi_snd.thresh, self.kiwi_snd.decay)

            if hasattr(self, 'control_deck'):
                 self.control_deck.smeter_label.setText(f"Signal: {int(rssi_smooth_slow)} dBm")
        except Exception as e:
            pass

    def poll_cat_radio(self):
        """Poll CAT radio for frequency/mode changes and sync to Kiwi if enabled"""
        if not self.cat_active or not self.cat_radio:
            return
        
        # Check if any sync is enabled
        freq_sync_enabled = self.control_deck.cat_sync_radio_to_kiwi_freq_cb.isChecked()
        mode_sync_enabled = self.control_deck.cat_sync_radio_to_kiwi_mode_cb.isChecked()
        
        if not freq_sync_enabled and not mode_sync_enabled:
            return
        
        try:
            # Get current frequency from radio if sync enabled
            if freq_sync_enabled:
                radio_freq = self.cat_radio.get_freq()
                
                if radio_freq and radio_freq != self.current_freq:
                    # Frequency changed on radio, update Kiwi
                    self.current_freq = radio_freq
                    
                    if self.kiwi_snd:
                        tuned_freq = radio_freq
                        if self.kiwi_snd.radio_mode == "CW":
                            tuned_freq -= CW_PITCH
                        self.kiwi_snd.freq = tuned_freq
                        self.kiwi_snd.set_mode_freq_pb()
                    
                    if self.wf_snd_link_flag and self.kiwi_wf:
                        self.kiwi_wf.set_freq_zoom(radio_freq, self.kiwi_wf.zoom)
                    
                    print(f"Synced frequency from radio: {radio_freq} kHz")
            
            # Sync mode from radio to Kiwi if enabled
            if mode_sync_enabled:
                radio_mode = self.cat_radio.get_mode()
                if radio_mode and radio_mode != self.current_mode:
                    # Update mode without triggering sync back to radio
                    self.current_mode = radio_mode
                    
                    if self.kiwi_snd:
                        self.kiwi_snd.radio_mode = radio_mode
                        
                        # Set default bandwidths for modes
                        if radio_mode == "CW":
                            default_bw = 500
                        elif radio_mode in ["AM", "NFM"]:
                            default_bw = 6000
                        else:  # SSB
                            default_bw = 2700
                            
                        self.control_deck.bw_slider.blockSignals(True)
                        self.control_deck.bw_slider.setValue(default_bw)
                        self.control_deck.bw_slider.blockSignals(False)
                        
                        self._apply_bandwidth(default_bw)
                    
                    if self.kiwi_wf:
                        self.kiwi_wf.radio_mode = radio_mode
                    
                    print(f"Synced mode from radio: {radio_mode}")
                    
        except Exception as e:
            # Don't spam errors, just fail silently
            pass

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if self.kiwi_snd:
            mods = QApplication.keyboardModifiers()
            step = 1.0
            if mods & Qt.ShiftModifier:
                step = 10.0
            elif mods & Qt.ControlModifier:
                step = 0.1
            
            if delta > 0:
                self.kiwi_snd.freq += step
            else:
                self.kiwi_snd.freq -= step
            
            self.kiwi_snd.set_mode_freq_pb()
            self.current_freq = self.kiwi_snd.freq
            
            if self.wf_snd_link_flag and self.kiwi_wf:
                self.kiwi_wf.set_freq_zoom(self.kiwi_snd.freq, self.kiwi_wf.zoom)
        self.update_bar_info()
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        if self.kiwi_snd and self.kiwi_wf:
            if key == Qt.Key_Left:
                fast_tune = mods & Qt.ShiftModifier
                slow_tune = mods & Qt.ControlModifier
                if self.kiwi_snd.radio_mode != "CW" and self.kiwi_wf.zoom < 10:
                    if fast_tune:
                        self.kiwi_snd.freq = self.kiwi_snd.freq //1 - 10
                    elif slow_tune:
                        self.kiwi_snd.freq = round(self.kiwi_snd.freq - 0.1, 2)
                    else:
                        self.kiwi_snd.freq = self.kiwi_snd.freq //1 if self.kiwi_snd.freq % 1 else self.kiwi_snd.freq //1 - 1
                else:
                    self.kiwi_snd.freq = round(self.kiwi_snd.freq - (1.0 if fast_tune else (0.01 if slow_tune else 0.1)), 2)
                self.kiwi_snd.set_mode_freq_pb()
                self.update_bar_info()

            elif key == Qt.Key_Right:
                fast_tune = mods & Qt.ShiftModifier
                slow_tune = mods & Qt.ControlModifier
                if self.kiwi_snd.radio_mode != "CW" and self.kiwi_wf.zoom < 10:
                    if fast_tune:
                        self.kiwi_snd.freq = self.kiwi_snd.freq //1 + 10
                    elif slow_tune:
                        self.kiwi_snd.freq = self.kiwi_snd.freq + 0.1
                    else:
                        self.kiwi_snd.freq = self.kiwi_snd.freq //1 + 1
                else:
                    self.kiwi_snd.freq = self.kiwi_snd.freq + (1.0 if fast_tune else (0.01 if slow_tune else 0.1))
                self.kiwi_snd.set_mode_freq_pb()
                self.update_bar_info()

            elif key == Qt.Key_Up:
                if self.kiwi_wf.zoom < self.kiwi_wf.MAX_ZOOM:
                    self.kiwi_wf.set_freq_zoom(self.kiwi_snd.freq + (CW_PITCH if self.kiwi_snd.radio_mode == "CW" else 0.), self.kiwi_wf.zoom + 1)

            elif key == Qt.Key_Down:
                if self.kiwi_wf.zoom > 0:
                    self.kiwi_wf.set_freq_zoom(self.kiwi_snd.freq + (CW_PITCH if self.kiwi_snd.radio_mode == "CW" else 0.), self.kiwi_wf.zoom - 1)

            elif key == Qt.Key_PageUp:
                manual_wf_freq = self.kiwi_wf.freq + self.kiwi_wf.span_khz / 4
                self.kiwi_wf.set_freq_zoom(manual_wf_freq, self.kiwi_wf.zoom)

            elif key == Qt.Key_PageDown:
                manual_wf_freq = self.kiwi_wf.freq - self.kiwi_wf.span_khz / 4
                self.kiwi_wf.set_freq_zoom(manual_wf_freq, self.kiwi_wf.zoom)

            elif key == Qt.Key_Space:
                if self.cat_snd_link_flag and self.cat_radio:
                    self.kiwi_wf.set_freq_zoom(self.cat_radio.freq, self.kiwi_wf.zoom)
                else:
                    self.kiwi_wf.set_freq_zoom(self.kiwi_snd.freq, self.kiwi_wf.zoom)

            elif key == Qt.Key_U:
                self.kiwi_snd.radio_mode = "USB"
                self.kiwi_snd.set_mode_freq_pb()

            elif key == Qt.Key_L:
                self.kiwi_snd.radio_mode = "LSB"
                self.kiwi_snd.set_mode_freq_pb()

            elif key == Qt.Key_C:
                self.kiwi_snd.radio_mode = "CW"
                self.kiwi_snd.set_mode_freq_pb()

            elif key == Qt.Key_A:
                self.kiwi_snd.radio_mode = "AM"
                self.kiwi_snd.set_mode_freq_pb()

            elif key == Qt.Key_Z:
                self.wf_snd_link_flag = not self.wf_snd_link_flag
                self.kiwi_wf.set_freq_zoom(self.kiwi_snd.freq, self.kiwi_wf.zoom)

            elif key == Qt.Key_X:
                self.auto_mode = not self.auto_mode
                if self.auto_mode and get_auto_mode:
                    self.kiwi_snd.radio_mode = get_auto_mode(self.kiwi_snd.freq)
                    self.kiwi_snd.set_mode_freq_pb()

            elif key == Qt.Key_G:
                if self.kiwi_wf.averaging_n < 100:
                    self.kiwi_wf.averaging_n += 1

            elif key == Qt.Key_H:
                if self.kiwi_wf.averaging_n > 1:
                    self.kiwi_wf.averaging_n -= 1

            elif key == Qt.Key_V:
                if mods & Qt.ShiftModifier:
                    self.kiwi_snd.volume = 0
                else:
                    if self.kiwi_snd.volume < 150:
                        self.kiwi_snd.volume += 10

            elif key == Qt.Key_B:
                if self.kiwi_snd.volume > 0:
                    self.kiwi_snd.volume -= 10

            elif key == Qt.Key_M:
                if mods & Qt.ShiftModifier:
                    self.show_mem_flag = not self.show_mem_flag
                else:
                    self.s_meter_show_flag = not self.s_meter_show_flag
                    self.s_meter_widget.setVisible(self.s_meter_show_flag)

            elif key == Qt.Key_D:
                self.show_dxcluster_flag = not self.show_dxcluster_flag

            elif key == Qt.Key_I:
                self.show_eibi_flag = not self.show_eibi_flag

            elif key == Qt.Key_S:
                self.cat_snd_link_flag = not self.cat_snd_link_flag

        self.update_bar_info()
        super().keyPressEvent(event)


def main():
    app = QApplication(sys.argv)
    
    # Instantiate kiwi_list manager before parsing options,
    # as the dialog might use command line defaults if it's the first run.
    kiwi_list_manager = utils_supersdr.kiwi_list()

    # Original option parsing logic
    parser = OptionParser()
    parser.add_option("-w", "--password", type=str,
                      help="KiwiSDR password", dest="kiwipassword", default=default_kiwi_password)
    parser.add_option("-s", "--kiwiserver", type=str,
                      help="KiwiSDR server name", dest="kiwiserver", default=default_kiwi_server)
    parser.add_option("-p", "--kiwiport", type=int,
                      help="port number", dest="kiwiport", default=default_kiwi_port)
    parser.add_option("-z", "--zoom", type=int,
                      help="zoom factor", dest="zoom", default=8)
    parser.add_option("-f", "--freq", type=float,
                      help="center frequency in kHz", dest="freq", default=None)
    parser.add_option("-c", "--callsign", type=str,
                      help="DXCluster callsign", dest="callsign", default="")
    parser.add_option("--rigctld-host", type=str,
                      help="rigctld host (default: localhost)", dest="rigctld_host", default="localhost")
    parser.add_option("--rigctld-port", type=int,
                      help="rigctld port (default: 4532)", dest="rigctld_port", default=4532)

    (parsed_options, args) = parser.parse_args()
    
    options_dict = vars(parsed_options)
    
    if not options_dict['kiwiserver'] or options_dict['kiwiserver'] == default_kiwi_server:
        print("No KiwiSDR server specified via command line, or default used. Opening selection dialog.")
        kiwi_host, kiwi_port, kiwi_password, connect_new_flag = kiwi_list_manager.choose_kiwi_dialog()

        if connect_new_flag:
            options_dict['kiwiserver'] = kiwi_host
            options_dict['kiwiport'] = kiwi_port
            options_dict['kiwipassword'] = kiwi_password
        else:
            print("KiwiSDR selection cancelled. Exiting.")
            sys.exit(1)
    
    window = SuperSDRMainWindow(options_dict, options_dict['callsign'])
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
