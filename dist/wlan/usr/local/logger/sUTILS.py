import logging
import logging.handlers
from collections import deque
from queue import Queue, Full, Empty
import paho.mqtt.client as mqtt
import threading
import numpy as np
import struct
import serial
import socket
import inspect
import os

class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]
    
class LimitedQueue(Queue):
    def __init__(self, maxsize=256):
        super().__init__(maxsize)
        
    def put(self, data, block=True, timeout=None):
        try:
            super().put(data, block=False)  # block=False로 설정하여 비동기적으로 큐에 데이터를 추가
        except Full:
            print("Queue is full. Processing existing data...")
            head = self.get()
            #print(f"data: {head}")
            try:
                super().put(data, block=False)  # 다시 시도하여 큐에 데이터를 추가
            except Full:
                print("Queue is still full after processing. Discarding data.")
            
    def get(self, block=True, timeout=None):
        try:
            return super().get(block, timeout)
        except Empty:
            return None

    def clear(self):
        while not self.empty():
            self.get_nowait()

    def is_empty(self):
        return self.empty()

class LimitedDeque(deque):
    def __init__(self, maxlen=8):
        super().__init__(maxlen=maxlen)
        self.maxlen = maxlen

    def put(self, item):
        if len(self) >= self.maxlen:
            raise Full("Deque has reached its maximum size.")
        self.append(item)

    def get(self):
        if len(self) == 0:
            return None
        return self.popleft()

    def clear(self):
        self.clear()

    def is_empty(self):
        return len(self) == 0
    
    def get_deque_byte_length(self):
        total_length = sum(len(item) for item in self)
        return total_length
        
    def get_deque_contents_as_bytearray(self):
        combined_data = bytearray()
        for item in self:
            combined_data.extend(item)
        return combined_data

class MQTTClient:
    def __init__(self, host, port, logger, max_retries=None, retry_delay=3):
        self.host = host
        self.port = port
        self.logger = logger
        self.retries = 0
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.connected = False

        self.mqttc = mqtt.Client()
        self.mqttc.on_connect = self.on_connect
        self.mqttc.on_disconnect = self.on_disconnect

        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.do)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join()
        self.logger.message('warn', "Mqtt closed")

    def do(self):
        self.logger.message('warn', "Mqtt thread start")
        self.logger.message('warn', f"Mqtt Attempting to connect to {self.host}:{self.port} (Attempt {self.retries + 1}/{self.max_retries if self.max_retries is not None else 'INF'})")
        self.mqttc.connect_async(self.host, self.port, keepalive=self.retry_delay)
        self.mqttc.loop_start()
        try:
            while not self.stop_event.is_set():
                if not self.connected:
                    cnt = int(self.retry_delay / 0.1)
                    for _ in range(cnt):
                        self.stop_event.wait(0.1)
                        if self.stop_event.is_set():
                            break
                        elif self.connected:
                            break
                    if not self.connected:
                        self.retries += 1
                        if self.max_retries is not None and self.retries >= self.max_retries:
                            self.logger.message('warn', "Mqtt Max retries reached")
                            break
                else:
                    self.stop_event.wait(self.retry_delay)
        finally:
            self.mqttc.disconnect()
            self.mqttc.loop_stop()
            self.logger.message('warn', "Mqtt thread exit")

    def sendData(self, topic, data):
        if self.connected == True:
            self.mqttc.publish(topic, data)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.logger.message('warn', f"Mqtt conneted")
            self.retries = 0
            self.connected = True
        else:
            self.logger.message('warn', f"Mqtt Connect failed with result code {rc}")

    def on_disconnect(self, client, userdata, rc):
        if self.connected:
            self.logger.message('warn', "Mqtt disconnected")
            self.connected = False

class Logger():
    def __init__(self, app_name='appName', facility=logging.handlers.SysLogHandler.LOG_LOCAL0, log_level='info', dbg_level='warn', log_print="off"):

        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            
        self.logger = logging.getLogger(app_name)
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)

        self.log_print = log_print
        self.log_prints = {
            'off': 0,
            'on' : 1
        }
        self.log_level = log_level
        self.dbg_level = dbg_level
        self.levels = {
            'emerg': 60,
            'alert': 55,
            'crit': logging.CRITICAL,
            'err': logging.ERROR,
            'warn': logging.WARNING,
            'notice': 25,
            'info': logging.INFO,
            'debug': logging.DEBUG,
            'notset': logging.NOTSET,
        }

        if not hasattr(logging, 'EMERG'):
            logging.addLevelName(60, 'EMERG')
        if not hasattr(logging, 'ALERT'):
            logging.addLevelName(55, 'ALERT')
        if not hasattr(logging, 'NOTICE'):
            logging.addLevelName(25, 'NOTICE')
        
        self.logger.setLevel(logging.DEBUG)
        
        self.syslog_handler = logging.handlers.SysLogHandler(address='/dev/log', facility=facility)
        self.syslog_handler.setLevel(logging.INFO)
        #self.formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
        formatter = logging.Formatter('%(name)s [%(custom_filename)s:%(custom_lineno)d] %(message)s')
        self.syslog_handler.setFormatter(formatter)
        self.logger.addHandler(self.syslog_handler)
        
        self.stream_handler = logging.StreamHandler()
        self.stream_handler.setLevel(logging.INFO)
        log_format = '%(asctime)s.%(msecs)03d [%(name)s][%(levelname)s][%(custom_filename)s:%(custom_lineno)d] %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'
        formatter = logging.Formatter(log_format, datefmt=date_format)
        
        #formatter = logging.Formatter('[%(name)s][%(levelname)s][%(custom_filename)s:%(custom_lineno)d] %(message)s')
        self.stream_handler.setFormatter(formatter)
        if self.log_print == 'on':
            self.logger.addHandler(self.stream_handler)
    
    def message(self, level, message, _extra=None):
        if level in self.levels:
            if _extra is None:
                self.logger.log(self.levels[level], message)
            else:
                self.logger.log(self.levels[level], message, extra={'custom_filename': _extra[0], 'custom_lineno': _extra[1]})
        else:
            if _extra is None:
                self.logger.log(self.levels[level], message)
            else:
                self.message('err', f"Unknown logging level: {level}", _EXTRA_())

    def no_extra(self):
        formatter = logging.Formatter('%(name)s %(message)s')
        self.syslog_handler.setFormatter(formatter)
            
    def get_logger(self):
        return self.logger

    def set_log_print(self, log_print):
        if log_print in self.log_prints:
            if log_print == 'on' and self.log_print == 'off':
                self.logger.addHandler(self.stream_handler)
            elif log_print == 'off' and self.log_print == 'on':
                self.logger.removeHandler(self.stream_handler)
            self.log_print = log_print
            self.message('warn', f"log print: {self.log_print}", _EXTRA_())
        else:
            self.message('err', f"Unknown print: {log_print}", _EXTRA_())
            
    def set_log_level(self, level):
        if level in self.levels:
            self.log_level = level
            self.message('warn', f"log_level: {self.log_level}, {self.levels.get(self.log_level, logging.INFO)}", _EXTRA_())
            #self.logger.setLevel(self.levels.get(self.log_level, logging.INFO))
            self.syslog_handler.setLevel(self.levels.get(self.log_level, logging.INFO))
        else:
            self.message('err', f"Unknown level: {level}", _EXTRA_())

    def set_dbg_level(self, level):
        if level in self.levels:
            self.dbg_level = level
            self.message('warn', f"dbg_level: {self.dbg_level}, {self.levels.get(self.dbg_level, logging.INFO)}", _EXTRA_())
            self.stream_handler.setLevel(self.levels.get(self.dbg_level, logging.INFO))
        else:
            self.message('err', f"Unknown level: {level}", _EXTRA_())
            
def _EXTRA_():
    frame = inspect.currentframe().f_back
    #file_name = frame.f_code.co_filename
    file_name = os.path.basename(frame.f_code.co_filename)
    line_number = frame.f_lineno
    return file_name, line_number

def _LINE_():
    frame = inspect.currentframe().f_back
    line_number = frame.f_lineno
    return line_number

def calculate_checksum(data):
    checksum = sum(data) % 256
    return checksum
    
def to_3byte_int(val):
    """Convert an integer to 3-byte representation using 2's complement for negative values."""
    if not (-8388608 <= val <= 8388607):
        raise ValueError("Value out of range for 3-byte integer")
    if val < 0:
        val = (1 << 24) + val
    return struct.pack('!I', val)[1:]

def downsample(data, factor):
    return data[::factor]

def add_to_buffer(b, data: bytes):
    b.extend(data)

def clear_buffer(b):
    b.clear()

def get_buffer_contents(b):
    return b
    
def get_buffer_contents(d):
    return list(d)

def shift_and_convert(array):
    # 8비트 왼쪽으로 쉬프트
    shifted_left = np.left_shift(array, 8)

    # 더블형으로 변환
    converted = shifted_left.astype(np.float64)

    # 8비트 오른쪽으로 쉬프트
    shifted_right = np.right_shift(converted.astype(np.int64), 8)

    return shifted_right
    
def parse_s24be_to_32bit(data):
    # Convert bytes to numpy array of int32
    s24_data = np.frombuffer(data, dtype=np.uint8)
        
    # Reshape to a 2D array where each row is a 24-bit sample (3 bytes)
    s24_data = s24_data.reshape(-1, 3)
        
    # Convert 24-bit data to 32-bit signed integer
    s32_data = ((s24_data[:, 2].astype(np.int32)) |
                (s24_data[:, 1].astype(np.int32) << 8) |
                (s24_data[:, 0].astype(np.int32) << 16))
    
    # Adjust for sign bit
    sign_mask = 0x800000
    s32_data[s32_data & sign_mask != 0] -= 0x1000000

    return s32_data
    
def parse_s24le_to_32bit(data):
    # Convert bytes to numpy array of int32
    s24_data = np.frombuffer(data, dtype=np.uint8)
        
    # Reshape to a 2D array where each row is a 24-bit sample (3 bytes)
    s24_data = s24_data.reshape(-1, 3)
        
    # Convert 24-bit data to 32-bit signed integer
    s32_data = (s24_data[:, 0].astype(np.int32) |
                (s24_data[:, 1].astype(np.int32) << 8) |
                (s24_data[:, 2].astype(np.int32) << 16))
        
    # Adjust for sign bit
    sign_mask = 0x800000
    s32_data[s32_data & sign_mask != 0] -= 0x1000000

    return s32_data

def convert_to_hex(self, data):
    if isinstance(data, np.ndarray):
        # numpy 배열인 경우
        return data.tobytes().hex()
    elif isinstance(data, bytes):
        # 이미 bytes 객체인 경우
        return data.hex()
    else:
        raise TypeError("data는 numpy 배열이나 bytes 객체여야 합니다.")
        
def convert_to_hex_float(self,data):
    if isinstance(data, np.ndarray):
        # numpy 배열을 리틀엔디안으로 바이트 변환
        return data.astype('<f4').tobytes().hex()
    elif isinstance(data, bytes):
        # 이미 bytes 객체인 경우
        return data.hex()
    else:
        raise TypeError("data는 numpy 배열이나 bytes 객체여야 합니다.")

def hex_to_float_array(self, hex_str, dtype=np.float32):
    # 헥사 문자열을 바이트로 변환
    byte_data = bytes.fromhex(hex_str)
    # 바이트 데이터를 리틀엔디안 numpy 배열로 변환
    float_array = np.frombuffer(byte_data, dtype='<f4')
    return float_array

def hex_to_float_list(self, hex_str):
    # 헥사 문자열을 바이트로 변환
    byte_data = bytes.fromhex(hex_str)
    # 4바이트씩 나누기
    floats = [struct.unpack('<f', byte_data[i:i+4])[0] for i in range(0, len(byte_data), 4)]
    return floats
    
#def find_char(buffer: bytes, char: str) -> int:
#    if len(char) != 1:
#        return -1
#    try:
#        index = buffer.index(char.encode('utf-8'))
#        return index
#    except ValueError:
#        return -1

def extract_data(buffer, start, length):
    if len(buffer) < start + length:
        return None
    for _ in range(start):
        buffer.popleft()
    data = bytes([buffer.popleft() for _ in range(length)])
    return data

    
def group_data(tdata, group_size=3):
    grouped_data = []
    for i in range(0, len(tdata), group_size):
        group = tdata[i:i + group_size]
        grouped_data.append(group)
    return grouped_data
    
def check_tcp_keepalive(self, host, port):
    try:
        #logger.message('warn', f"TCP connect")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect((host, port))
        # TCP Keepalive 설정
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # OS 별로 더 세부 설정 가능
        # s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 1)
        # s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)
        # s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
        return True
    except (socket.timeout, socket.error) as e:
        #logger.message('err', f"TCP connect fail: {e}")
        return False
    finally:
        s.close()
        
def check_udp_binding(self, port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(('', port))
        return True
    except (socket.error) as e:
        #logger.message('err', f"UDP bind fail: {e}")
        return False
    finally:
        s.close()
        
def check_serial_connection(self, devname, baud):
    try:
        s = serial.Serial(devname, baud, timeout=10)
        if s.is_open:
            return True
    except (serial.SerialException, serial.SerialTimeoutException) as e:
        #logger.message('err', f"serial connect fail: {e}")
        return False
    finally:
        if 's' in locals() and s.is_open:
            s.close()
                 
def check_connection(self, interface):
    if isinstance(interface, serial.Serial):
        #logging.warning("serial")
        return interface.is_open
    elif isinstance(interface, socket.socket):
        if interface.type == socket.SOCK_STREAM:
            #logging.warn("tcp")
            try:
                # TCP 연결 상태 확인
                interface.send(b'')
                return True
            except socket.error:
                return False
        elif interface.type == socket.SOCK_DGRAM:
            #logging.warn("udp")
            return True
            '''
            try:
                # UDP 연결 상태 확인 (패킷 전송 및 응답 대기)
                interface.sendto(b'ping', (self.dev_ip, self.dev_port))
                interface.settimeout(2)
                data, _ = interface.recvfrom(1024)
                if data:
                    return True
            except socket.error:
                return False
            '''
    return False

def check_interface_type(self, interface):
    if isinstance(interface, serial.Serial):
        return "serial"
    elif isinstance(interface, socket.socket):
        if interface.type == socket.SOCK_STREAM:
            return "tcp"
        elif interface.type == socket.SOCK_DGRAM:
            return "udp"
    else:
        return "unknown"
