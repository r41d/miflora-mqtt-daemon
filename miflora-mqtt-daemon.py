#!/usr/bin/env python3

import ssl
import sys
import re
import json
import os.path
import argparse
import threading
from time import time, sleep, localtime, strftime
from collections import OrderedDict
from colorama import init as colorama_init
from colorama import Fore, Back, Style
from configparser import ConfigParser
from unidecode import unidecode
from miflora.miflora_poller import MiFloraPoller, MI_BATTERY, MI_CONDUCTIVITY, MI_LIGHT, MI_MOISTURE, MI_TEMPERATURE
from btlewrap import available_backends, BluepyBackend, GatttoolBackend, PygattBackend, BluetoothBackendException
import paho.mqtt.client as mqtt
import sdnotify

MQTT_DEVICES_TOPIC = 'miflora/devices/#'

project_name = 'Xiaomi Mi Flora Plant Sensor MQTT Client/Daemon'
project_url = 'https://github.com/ThomDietrich/miflora-mqtt-daemon'

parameters = OrderedDict([
    (MI_LIGHT, dict(name="LightIntensity", name_pretty='Sunlight Intensity', typeformat='%d', unit='lux', device_class="illuminance")),
    (MI_TEMPERATURE, dict(name="AirTemperature", name_pretty='Air Temperature', typeformat='%.1f', unit='°C', device_class="temperature")),
    (MI_MOISTURE, dict(name="SoilMoisture", name_pretty='Soil Moisture', typeformat='%d', unit='%', device_class="humidity")),
    (MI_CONDUCTIVITY, dict(name="SoilConductivity", name_pretty='Soil Conductivity/Fertility', typeformat='%d', unit='µS/cm')),
    (MI_BATTERY, dict(name="Battery", name_pretty='Sensor Battery Level', typeformat='%d', unit='%', device_class="battery"))
])


class ReportingMode:
    def __init__(self):
        self.base_topic = "miflora"
        self.mqtt = True

    def set_will(self, mqtt_client, base_topic, device_id):
        pass

    def discovery_announcement(self, mqtt_client, base_topic, device_id, flores):
        pass

    def publish(self, mqtt_client, base_topic, flora_name, data):
        pass


class MqttJson(ReportingMode):
    def __init__(self):
        super().__init__()

    def set_will(self, mqtt_client, base_topic, device_id):
        mqtt_client.will_set('{}/$announce'.format(base_topic), payload='{}', retain=True)

    def discovery_announcement(self, mqtt_client, base_topic, device_id, flores):
        print_line('Announcing Mi Flora devices to MQTT broker for auto-discovery ...')
        flores_info = dict()
        for [flora_name, flora] in flores.items():
            flora_info = {key: value for key, value in flora.items() if key not in ['poller', 'stats']}
            flora_info['topic'] = '{}/{}'.format(base_topic, flora_name)
            flores_info[flora_name] = flora_info
        mqtt_client.publish('{}/$announce'.format(base_topic), json.dumps(flores_info), retain=True)
        sleep(0.5)  # some slack for the publish roundtrip and callback function
        print()

    def publish(self, mqtt_client, base_topic, flora_name, data):
        print_line('Publishing to MQTT topic "{}/{}"'.format(base_topic, flora_name))
        mqtt_client.publish('{}/{}'.format(base_topic, flora_name), json.dumps(data))
        sleep(0.5)  # some slack for the publish roundtrip and callback function


class MqttHomie(ReportingMode):
    def __init__(self):
        super().__init__()
        self.base_topic = "homie"

    def set_will(self, mqtt_client, base_topic, device_id):
        mqtt_client.will_set('{}/{}/$online'.format(base_topic, device_id), payload='false', retain=True)

    def discovery_announcement(self, mqtt_client, base_topic, device_id, flores):
        print_line('Announcing Mi Flora devices to MQTT broker for auto-discovery ...')
        mqtt_client.publish('{}/{}/$homie'.format(base_topic, device_id), '2.1.0-alpha', 1, True)
        mqtt_client.publish('{}/{}/$online'.format(base_topic, device_id), 'true', 1, True)
        mqtt_client.publish('{}/{}/$name'.format(base_topic, device_id), device_id, 1, True)
        mqtt_client.publish('{}/{}/$fw/version'.format(base_topic, device_id), flores[0]['firmware'], 1, True)

        nodes_list = ','.join([flora_name for [flora_name, flora] in flores.items()])
        mqtt_client.publish('{}/{}/$nodes'.format(base_topic, device_id), nodes_list, 1, True)

        for [flora_name, flora] in flores.items():
            topic_path = '{}/{}/{}'.format(base_topic, device_id, flora_name)
            mqtt_client.publish('{}/$name'.format(topic_path), flora['name_pretty'], 1, True)
            mqtt_client.publish('{}/$type'.format(topic_path), 'miflora', 1, True)
            mqtt_client.publish('{}/$properties'.format(topic_path), 'battery,conductivity,light,moisture,temperature', 1, True)
            mqtt_client.publish('{}/battery/$settable'.format(topic_path), 'false', 1, True)
            mqtt_client.publish('{}/battery/$unit'.format(topic_path), 'percent', 1, True)
            mqtt_client.publish('{}/battery/$datatype'.format(topic_path), 'int', 1, True)
            mqtt_client.publish('{}/battery/$range'.format(topic_path), '0:100', 1, True)
            mqtt_client.publish('{}/conductivity/$settable'.format(topic_path), 'false', 1, True)
            mqtt_client.publish('{}/conductivity/$unit'.format(topic_path), 'µS/cm', 1, True)
            mqtt_client.publish('{}/conductivity/$datatype'.format(topic_path), 'int', 1, True)
            mqtt_client.publish('{}/conductivity/$range'.format(topic_path), '0:*', 1, True)
            mqtt_client.publish('{}/light/$settable'.format(topic_path), 'false', 1, True)
            mqtt_client.publish('{}/light/$unit'.format(topic_path), 'lux', 1, True)
            mqtt_client.publish('{}/light/$datatype'.format(topic_path), 'int', 1, True)
            mqtt_client.publish('{}/light/$range'.format(topic_path), '0:50000', 1, True)
            mqtt_client.publish('{}/moisture/$settable'.format(topic_path), 'false', 1, True)
            mqtt_client.publish('{}/moisture/$unit'.format(topic_path), 'percent', 1, True)
            mqtt_client.publish('{}/moisture/$datatype'.format(topic_path), 'int', 1, True)
            mqtt_client.publish('{}/moisture/$range'.format(topic_path), '0:100', 1, True)
            mqtt_client.publish('{}/temperature/$settable'.format(topic_path), 'false', 1, True)
            mqtt_client.publish('{}/temperature/$unit'.format(topic_path), '°C', 1, True)
            mqtt_client.publish('{}/temperature/$datatype'.format(topic_path), 'float', 1, True)
            mqtt_client.publish('{}/temperature/$range'.format(topic_path), '*', 1, True)
        sleep(0.5)  # some slack for the publish roundtrip and callback function
        print()

    def publish(self, mqtt_client, base_topic, flora_name, data):
        print_line('Publishing data to MQTT base topic "{}/{}/{}"'.format(base_topic, device_id, flora_name))
        for [param, value] in data.items():
            mqtt_client.publish('{}/{}/{}/{}'.format(base_topic, device_id, flora_name, param), value, 1, False)
        sleep(0.5)  # some slack for the publish roundtrip and callback function


class MqttSmarthome(ReportingMode):
    def __init__(self):
        super().__init__()

    def set_will(self, mqtt_client, base_topic, device_id):
        mqtt_client.will_set('{}/connected'.format(base_topic), payload='0', retain=True)

    def publish(self, mqtt_client, base_topic, flora_name, data):
        for [param, value] in data.items():
            print_line('Publishing data to MQTT topic "{}/status/{}/{}"'.format(base_topic, flora_name, param))
            payload = dict()
            payload['val'] = value
            payload['ts'] = int(round(time() * 1000))
            mqtt_client.publish('{}/status/{}/{}'.format(base_topic, flora_name, param), json.dumps(payload), retain=True)
        sleep(0.5)  # some slack for the publish roundtrip and callback function


class HomeassistantMqtt(ReportingMode):
    def __init__(self):
        super().__init__()
        self.base_topic = "homeassistant"

    def discovery_announcement(self, mqtt_client, base_topic, device_id, flores):
        print_line('Announcing Mi Flora devices to MQTT broker for auto-discovery ...')
        for [flora_name, flora] in flores.items():
            topic_path = '{}/sensor/{}'.format(base_topic, flora_name)
            base_payload = {
                "state_topic": "{}/state".format(topic_path).lower()
            }
            for sensor, params in parameters.items():
                payload = dict(base_payload.items())
                payload['unit_of_measurement'] = params['unit']
                payload['value_template'] = "{{ value_json.%s }}" % (sensor,)
                payload['name'] = "{} {}".format(flora_name, sensor.title())
                if 'device_class' in params:
                    payload['device_class'] = params['device_class']
                mqtt_client.publish('{}/{}_{}/config'.format(topic_path, flora_name, sensor).lower(), json.dumps(payload), 1, True)

    def publish(self, mqtt_client, base_topic, flora_name, data):
        print_line('Publishing to MQTT topic "{}/sensor/{}/state"'.format(base_topic, flora_name).lower())
        mqtt_client.publish('{}/sensor/{}/state'.format(base_topic, flora_name).lower(), json.dumps(data))
        sleep(0.5)  # some slack for the publish roundtrip and callback function


class ThingsboardJson(ReportingMode):
    def __init__(self):
        super().__init__()
        self.base_topic = "v1/devices/me/telemetry"

    def publish(self, mqtt_client, base_topic, flora_name, data):
        print_line('Publishing to MQTT topic "{}" username "{}"'.format(base_topic, flora_name))
        mqtt_client.username_pw_set(flora_name)
        mqtt_client.reconnect()
        sleep(1.0)
        mqtt_client.publish('{}'.format(base_topic), json.dumps(data))
        sleep(0.5)  # some slack for the publish roundtrip and callback function


class WirenboardMqtt(ReportingMode):
    def __init__(self):
        super().__init__()
        self.base_topic = ""

    def discovery_announcement(self, mqtt_client, base_topic, device_id, flores):
        print_line('Announcing Mi Flora devices to MQTT broker for auto-discovery ...')
        for [flora_name, flora] in flores.items():
            mqtt_client.publish('/devices/{}/meta/name'.format(flora_name), flora_name, 1, True)
            topic_path = '/devices/{}/controls'.format(flora_name)
            mqtt_client.publish('{}/battery/meta/type'.format(topic_path), 'value', 1, True)
            mqtt_client.publish('{}/battery/meta/units'.format(topic_path), '%', 1, True)
            mqtt_client.publish('{}/conductivity/meta/type'.format(topic_path), 'value', 1, True)
            mqtt_client.publish('{}/conductivity/meta/units'.format(topic_path), 'µS/cm', 1, True)
            mqtt_client.publish('{}/light/meta/type'.format(topic_path), 'value', 1, True)
            mqtt_client.publish('{}/light/meta/units'.format(topic_path), 'lux', 1, True)
            mqtt_client.publish('{}/moisture/meta/type'.format(topic_path), 'rel_humidity', 1, True)
            mqtt_client.publish('{}/temperature/meta/type'.format(topic_path), 'temperature', 1, True)
            mqtt_client.publish('{}/timestamp/meta/type'.format(topic_path), 'text', 1, True)
        sleep(0.5)  # some slack for the publish roundtrip and callback function
        print()

    def publish(self, mqtt_client, base_topic, flora_name, data):
        for [param, value] in data.items():
            print_line('Publishing data to MQTT topic "/devices/{}/controls/{}"'.format(flora_name, param))
            mqtt_client.publish('/devices/{}/controls/{}'.format(flora_name, param), value, retain=True)
        mqtt_client.publish('/devices/{}/controls/{}'.format(flora_name, 'timestamp'), strftime('%Y-%m-%d %H:%M:%S', localtime()), retain=True)
        sleep(0.5)  # some slack for the publish roundtrip and callback function


class Json(ReportingMode):
    def __init__(self):
        super().__init__()
        self.mqtt = False

    def publish(self, mqtt_client, base_topic, flora_name, data):  # client will be None here and topic is irrelevant
        data['timestamp'] = strftime('%Y-%m-%d %H:%M:%S', localtime())
        data['name'] = flora_name
        data['name_pretty'] = flora['name_pretty']
        data['mac'] = flora['mac']
        data['firmware'] = flora['firmware']
        print('Data for "{}": {}'.format(flora_name, json.dumps(data)))


reporting_modes_mapping = {
    'mqtt-json': MqttJson,
    'mqtt-homie': MqttHomie,
    'mqtt-smarthome': MqttSmarthome,
    'homeassistant-mqtt': HomeassistantMqtt,
    'thingsboard-json': ThingsboardJson,
    'wirenboard-mqtt': WirenboardMqtt,
    'json': Json,
}


if False:
    # will be caught by python 2.7 to be illegal syntax
    print('Sorry, this script requires a python3 runtime environemt.', file=sys.stderr)

# Argparse
parser = argparse.ArgumentParser(description=project_name, epilog='For further details see: ' + project_url)
parser.add_argument('--gen-openhab', help='generate openHAB items based on configured sensors', action='store_true')
parser.add_argument('--config_dir', help='set directory where config.ini is located', default=sys.path[0])
parse_args = parser.parse_args()

# Intro
colorama_init()
print(Fore.GREEN + Style.BRIGHT)
print(project_name)
print('Source:', project_url)
print(Style.RESET_ALL)

# Systemd Service Notifications - https://github.com/bb4242/sdnotify
sd_notifier = sdnotify.SystemdNotifier()


# Logging function
def print_line(text, error = False, warning=False, sd_notify=False, console=True):
    timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime())
    if console:
        if error:
            print(Fore.RED + Style.BRIGHT + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL, file=sys.stderr)
        elif warning:
            print(Fore.YELLOW + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
        else:
            print(Fore.GREEN + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
    timestamp_sd = strftime('%b %d %H:%M:%S', localtime())
    if sd_notify:
        sd_notifier.notify('STATUS={} - {}.'.format(timestamp_sd, unidecode(text)))


# Identifier cleanup
def clean_identifier(name):
    clean = name.strip()
    for this, that in [[' ', '-'], ['ä', 'ae'], ['Ä', 'Ae'], ['ö', 'oe'], ['Ö', 'Oe'], ['ü', 'ue'], ['Ü', 'Ue'], ['ß', 'ss']]:
        clean = clean.replace(this, that)
    clean = unidecode(clean)
    return clean


# Eclipse Paho callbacks - http://www.eclipse.org/paho/clients/python/docs/#callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print_line('MQTT connection established, now subscribing to topics', console=True, sd_notify=True)

        mqtt_client.subscribe(MQTT_DEVICES_TOPIC)
        mqtt_client.message_callback_add(MQTT_DEVICES_TOPIC, new_device_callback)

        print()
    else:
        print_line('Connection error with result code {} - {}'.format(str(rc), mqtt.connack_string(rc)), error=True)
        os._exit(1)


flores_from_mqtt = OrderedDict()
flores_from_mqtt_lock = threading.RLock()


def new_device_callback(client, userdata, message):
    print_line('new_device_callback %s %s' % (message.topic, message.payload))
    name = message.topic.split('/')[-1]
    mac = message.payload.decode("utf-8")
    with flores_from_mqtt_lock:
        flores_from_mqtt[name] = mac


def flores_to_openhab_items(flores, reporting_mode):
    print_line('Generating openHAB items. Copy to your configuration and modify as needed...')
    items = list()
    items.append('// miflora.items - Generated by miflora-mqtt-daemon.')
    items.append('// Adapt to your needs! Things you probably want to modify:')
    items.append('//     Room group names, icons,')
    items.append('//     "gAll", "broker", "UnknownRoom"')
    items.append('')
    items.append('// Mi Flora specific groups')
    items.append('Group gMiFlora "All Mi Flora sensors and elements" (gAll)')
    for param, param_properties in parameters.items():
        items.append('Group g{} "Mi Flora {} elements" (gAll, gMiFlora)'.format(param_properties['name'], param_properties['name_pretty']))
    if reporting_mode == 'mqtt-json':
        for [flora_name, flora] in flores.items():
            location = flora['location_clean'] if flora['location_clean'] else 'UnknownRoom'
            items.append('\n// Mi Flora "{}" ({})'.format(flora['name_pretty'], flora['mac']))
            items.append('Group g{}{} "Mi Flora Sensor {}" (gMiFlora, g{})'.format(location, flora_name, flora['name_pretty'], location))
            for [param, param_properties] in parameters.items():
                basic = 'Number {}_{}_{}'.format(location, flora_name, param_properties['name'])
                label = '"{} {} {} [{} {}]"'.format(location, flora['name_pretty'], param_properties['name_pretty'], param_properties['typeformat'], param_properties['unit'].replace('%', '%%'))
                details = '<text> (g{}{}, g{})'.format(location, flora_name, param_properties['name'])
                channel = '{{mqtt="<[broker:{}/{}:state:JSONPATH($.{})]"}}'.format(base_topic, flora_name, param)
                items.append(' '.join([basic, label, details, channel]))
        items.append('')
        print('\n'.join(items))
    else:
        raise IOError('Given reporting_mode not supported for the export to openHAB items')


# Load configuration file
config_dir = parse_args.config_dir

config = ConfigParser(delimiters=('=', ))
config.optionxform = str
config.read([os.path.join(config_dir, 'config.ini.dist'), os.path.join(config_dir, 'config.ini')])

reporting_mode = config['General'].get('reporting_method', 'mqtt-json')

# Check reporting mode
if reporting_mode not in reporting_modes_mapping.keys():
    print_line('Configuration parameter reporting_mode set to an invalid value (%s)' % reporting_mode, error=True, sd_notify=True)
    sys.exit(1)

reporting_mode_obj = reporting_modes_mapping[reporting_mode]()
used_adapter = config['General'].get('adapter', 'hci0')
daemon_enabled = config['Daemon'].getboolean('enabled', True)

default_base_topic = reporting_mode_obj.base_topic

base_topic = config['MQTT'].get('base_topic', default_base_topic).lower()
device_id = config['MQTT'].get('homie_device_id', 'miflora-mqtt-daemon').lower()
sleep_period = config['Daemon'].getint('period', 300)
miflora_cache_timeout = config['Sensors'].getint('timeout', 10)

# Check configuration
if not config['Sensors']:
    print_line('No sensors found in configuration file "config.ini", running without any for now...', error=True, sd_notify=True)
    # sys.exit(1)
if reporting_mode == 'wirenboard-mqtt' and base_topic:
    print_line('Parameter "base_topic" ignored for "reporting_method = wirenboard-mqtt"', warning=True, sd_notify=True)


print_line('Configuration accepted', console=False, sd_notify=True)


# MQTT connection
if reporting_mode_obj.mqtt:
    print_line('Connecting to MQTT broker ...')
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_connect

    reporting_mode_obj.set_will(mqtt_client, base_topic, device_id)

    if config['MQTT'].getboolean('tls', False):
        # According to the docs, setting PROTOCOL_SSLv23 "Selects the highest protocol version
        # that both the client and server support. Despite the name, this option can select
        # “TLS” protocols as well as “SSL”" - so this seems like a resonable default
        mqtt_client.tls_set(
            ca_certs=config['MQTT'].get('tls_ca_cert', None),
            keyfile=config['MQTT'].get('tls_keyfile', None),
            certfile=config['MQTT'].get('tls_certfile', None),
            tls_version=ssl.PROTOCOL_SSLv23
        )

    if config['MQTT'].get('username'):
        mqtt_client.username_pw_set(config['MQTT'].get('username'), config['MQTT'].get('password', None))
    try:
        mqtt_client.connect(config['MQTT'].get('hostname', 'localhost'),
                            port=config['MQTT'].getint('port', 1883),
                            keepalive=config['MQTT'].getint('keepalive', 60))
    except:
        print_line('MQTT connection error. Please check your settings in the configuration file "config.ini"', error=True, sd_notify=True)
        sys.exit(1)
    else:
        if reporting_mode == 'mqtt-smarthome':
            mqtt_client.publish('{}/connected'.format(base_topic), payload='1', retain=True)
        if reporting_mode != 'thingsboard-json':
            mqtt_client.loop_start()
            sleep(1.0) # some slack to establish the connection

sd_notifier.notify('READY=1')


def add_flower_sensor(flores, name, mac):
    if not re.match("C4:7C:8D:[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}", mac):
        print_line('The MAC address "{}" seems to be in the wrong format!'.format(mac), error=True, sd_notify=True)
        return flores
        #sys.exit(1)

    if '@' in name:
        name_pretty, location_pretty = name.split('@')
    else:
        name_pretty, location_pretty = name, ''
    name_clean = clean_identifier(name_pretty)
    location_clean = clean_identifier(location_pretty)

    flora = dict()
    print_line('Adding sensor to device list and testing connection ...')
    print_line('Name:          "{}"'.format(name_pretty))
    #print_line('Attempting initial connection to Mi Flora sensor "{}" ({})'.format(name_pretty, mac), console=False, sd_notify=True)

    flora_poller = MiFloraPoller(mac=mac, backend=GatttoolBackend, cache_timeout=miflora_cache_timeout, retries=3, adapter=used_adapter)
    flora['poller'] = flora_poller
    flora['name_pretty'] = name_pretty
    flora['mac'] = flora_poller._mac
    flora['refresh'] = sleep_period
    flora['location_clean'] = location_clean
    flora['location_pretty'] = location_pretty
    flora['stats'] = {"count": 0, "success": 0, "failure": 0}
    try:
        flora_poller.fill_cache()
        flora_poller.parameter_value(MI_LIGHT)
        flora['firmware'] = flora_poller.firmware_version()
    except (IOError, BluetoothBackendException):
        print_line('Initial connection to Mi Flora sensor "{}" ({}) failed.'.format(name_pretty, mac), error=True, sd_notify=True)
        return flores
    else:
        # print_line('Internal name: "{}"'.format(name_clean))
        # print_line('Device name:   "{}"'.format(flora_poller.name()))
        print_line('MAC address:   {}'.format(flora_poller._mac))
        # print_line('Firmware:      {}'.format(flora_poller.firmware_version()))
        print_line('Initial connection to Mi Flora sensor "{}" ({}) successful'.format(name_pretty, mac), sd_notify=True)
    print()
    flores[name_clean] = flora
    return flores

flores = OrderedDict()

# Initialize Mi Flora sensors from config
for [name, mac] in config['Sensors'].items():
    flores = add_flower_sensor(flores, name, mac)

# openHAB items generation
if parse_args.gen_openhab:
    flores_to_openhab_items(flores, reporting_mode)
    sys.exit(0)

# Discovery Announcement
reporting_mode_obj.discovery_announcement(mqtt_client, base_topic, device_id, flores)

print_line('Initialization complete, starting MQTT publish loop', console=False, sd_notify=True)


# Sensor data retrieval and publication
while True:

    new = False
    # Check for new Mi Flora sensors from MQTT configuration and initialize them
    with flores_from_mqtt_lock:
        for name, mac in flores_from_mqtt.items():
            if name not in flores:
                flores = add_flower_sensor(flores, name, mac)
                new = True
        if new:
            # Discovery Announcement
            reporting_mode_obj.discovery_announcement(mqtt_client, base_topic, device_id, flores)

    for [flora_name, flora] in flores.items():
        data = dict()
        attempts = 2
        flora['poller']._cache = None
        flora['poller']._last_read = None
        flora['stats']['count'] = flora['stats']['count'] + 1
        print_line('Retrieving data from sensor "{}" ...'.format(flora['name_pretty']))
        while attempts != 0 and not flora['poller']._cache:
            try:
                flora['poller'].fill_cache()
                flora['poller'].parameter_value(MI_LIGHT)
            except (IOError, BluetoothBackendException):
                attempts = attempts - 1
                if attempts > 0:
                    print_line('Retrying ...', warning = True)
                flora['poller']._cache = None
                flora['poller']._last_read = None

        if not flora['poller']._cache:
            flora['stats']['failure'] = flora['stats']['failure'] + 1
            print_line('Failed to retrieve data from Mi Flora sensor "{}" ({}), success rate: {:.0%}'.format(
                flora['name_pretty'], flora['mac'], flora['stats']['success']/flora['stats']['count']
            ), error=True, sd_notify=True)
            print()
            continue
        else:
            flora['stats']['success'] = flora['stats']['success'] + 1

        try:
            for param in parameters.keys():
                data[param] = flora['poller'].parameter_value(param)
        except BluetoothBackendException as bbe:
            print_line('Read fail on sensor %s' % flora_name, error=True)
        else:
            print_line('Result: {}'.format(json.dumps(data)))
            reporting_mode_obj.publish(mqtt_client, base_topic, flora_name, data)

        print()

    print_line('Status messages published', console=False, sd_notify=True)

    if daemon_enabled:
        print_line('Sleeping ({} seconds) ...'.format(sleep_period))
        sleep(sleep_period)
        print()
    else:
        print_line('Execution finished in non-daemon-mode', sd_notify=True)
        if reporting_mode == 'mqtt-json':
            mqtt_client.disconnect()
        break
