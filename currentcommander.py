#!/usr/bin/python3
from os.path import expanduser, exists
import getopt
import sys
import time
import socket
import configparser 
import urwid
import threading
from datetime import datetime
from pysnmp.hlapi import *
from pysnmp.proto import rfc1902

# for ANEL NetPwr REST API
import httplib2
# IPMI/Redfish BMC support
from pyghmi.ipmi import command as ipmi_command
from pyghmi.redfish import command as redfish_command
#from pyghmi.ipmi.command import Housekeeper
from pyghmi.ipmi.command import Command

# TODO/Ideas:
# - ATEN PDU support
#    - edit outlet names
#    - display configured outlet power on/off delays
#    - edit outlet power on/off delays
#    - display overcurrent protection status
#    - power usage plot
# - SNMP PoE switch support
#    - HP procurve PoE switch 2530 works
#    - show error conditions
#    - show traffic counter
#    - reset traffic counter
# - IPMI support
#    - display sensor data
#    - set device for next boot
#    - display event log 
# - implement edit dialog
# - fix toggling of outlets with pointer devices
#   - change outlet selection with single click
#      - provide config option to select if single or double click is required to toggle power
#   - handle double click (done)
# - add optional (config) confirmation dialog for power toggle 
# - add event log showing status changes and device communication
#
#
# Bugs
#
# - fix/test presets
# - fix error when ipmi device is not reachable 
#
# Higher priority features
# - create an indicator bar showing previous and next devices
# - rename outlets
# - set/unset ipmi bootdevice for next boot
# - ipmi sensor data when on
# - switch traffic data
# - a generic way to display arbitrary snmp tables
# - confirmation dialog for switching outlets
#   - global setting: no confirmation / only confirm off / confirm on/off
#

#
# Low priority / new features
#
# - reduce code duplication in SNMP device classes
# - auto refresh
# - introduce global settings section and dialog
#    - toggling between needs to be modified for this, because we rely on every config section being a device config
#    - switch_on_delay (may be better as device option?)
#    - refresh (may also be better as device option? That would simplify it at least for now) 
#    -> use default or device config option and be done with it
# - manage crontab to send on/off commands at scheduled times (apt: python-crontab, from crontab import CronTab)
# - allow to create dependency rules
#    - switch on outlet 1 and 2, of they were off, when outlet 3 is toggled (to reduce amp popping, when mixer is powered on)
# - create config file if none exists
#    - open powerstrip edit dialog, save, then reload ui
# - Support running configured shell commands on selected outlet, with device/outlet/config attributes usable in the command string
#      - example command definition in config: cmd1= /usr/sbin/xdg_open $outlet.current $outlet.voltage $device.ip
# - add bar graph for selectable metric V, A, W, Wh, state
#    - manually paint the lower half of the window. x: time, y: value
# - add way to toggle view mode for ATEN PDU
#    - mode 1: outlets, outlet details and presets 
#    - mode 2: outlets, outlet details and power graph for PDU
#  - use threads for all device communication - not really required, but a nice exercise
#
# Done
#
# - toggle between powerstrips (done)
# - add outlet detail view (done)
# - improve resize behaviour (done - switched back from raw display mode, because i'm not drawing freely (yet))
#    - this also solved exceptions causing the console to stay in a state that required a 'reset'  before it could be used after ^C 
# - fix selected list position is kept and fails to be applied when the next or previous powerstrip doesn't have enough items for a valid position (done)
# - remove old checkbox view (done)
#    - implement toggling with new list view (done)
#    - fix selected line changes to first line after reload (done)
# - fix ipmi connection timeout (done)
# - fix ipmi Housekeeper thread preventing clean shutdown (return to console without ^C after quit) (done)
# - ATEN PDU support
#    - make NetPwrCtrl and AtenPDU inherit same subclass (done) i think it was not required and i was too much in the java world
#       - it turned out it was a good decision, because the need to generalizes some functions arose
#         it is even required, because it contains the list of outlets in a single place to be used in generic form in the ui
#    - display states and names (done)
#    - toggle states (done)
#    - display power usage (done)
#    - display voltage (done)
#    - strictly use pysnmp.hlapi and no other modules from pysnmp (done) with one exception which is good
# - SNMP PoE switch support
#    - display PoE port alias and status (done)
#    - toggle PoE admin enabled status on/off per port (done)
#    - display IP/MAC addresses detected on the port (done, MACs)
# - IPMI support
#    - diplay state (done)
#    - toggle power (done)
#    - display boot device (done)
# - Redfish REST API support
#    - toggle power (done)
#





#_TABLE_COL_DEFS = [
#    ['{:<13}', 'Name'],
#    ['{:>6}', ,
#    '': '{:<13}',
#]


class ListItem(urwid.WidgetWrap):

    text = None
   
    def __init__ (self, o):
        urwid.register_signal(self.__class__, ['item_activated'])
        self.data = o
        #name = '{:<10}{:>6s}{:>7}{:>10}{:>11}'.format(
        state = 'off'
        if o['state'] == 1:
            state = 'on'
        name = '{:<20}{:<6s} '.format(o['name'], state)
        #if 'last_on' in o:
        #    name += '{:>12s}'.format(str(o['last_on'].strftime('%H:%M:%S')))
        #if 'last_off' in o:
        #    name += '{:>12s}'.format(str(o['last_off'].strftime('%H:%M:%S')))
        if 'on_delay' in o:
            name += '{:>9s}'.format(o['on_delay'])
        if 'off_delay' in o:
            name += '{:>10s}'.format(o['off_delay'])
        for column in ['voltage', 'current', 'power', 'powerDissipation']:
            if column in o:
                name += '{:>8.2f}'.format(float(o[column]))
        if 'type' in o:
            name += '{:<14s}'.format(o['type'])
        if 'mac_addrs' in o:
            if len(o['mac_addrs']) > 0:
                name += '{:>18s}'.format(o['mac_addrs'][0])
            else:
                name += '{:>18s}'.format(' ')
      
        if 'bootdev' in o:
            name += '{:>18s}'.format(o['bootdev'])
        
        self.text = name
        text = urwid.Text(name)
        t = urwid.AttrWrap(text, "outlet", "outlet_selected")
        urwid.WidgetWrap.__init__(self, t)

    def keypress(self, size, key):
        return key



    last_click_time = 0
    # prevents three successive clicks being detected as two double clicks
    clicks_since_last_double_click = 0
    def mouse_event(self, size, event, button, col, row, focus):
        """
        Send 'click' signal on button 1 press.
        """
        if button != 1 or not urwid.util.is_mouse_press(event):
            return False

        now = time.time()
        self.clicks_since_last_double_click += 1
        if now - self.last_click_time < 0.5 and self.clicks_since_last_double_click >= 2:
            self._emit('item_activated')
            self.clicks_since_last_double_click = 0

        self.last_click_time = now
        self._emit('click')
        return True


    def selectable (self):
        return True



class ListView(urwid.WidgetWrap):

    def __init__(self):
        urwid.register_signal(self.__class__, ['show_details', 'item_activated'])
        self.walker = urwid.SimpleFocusListWalker([])
        self.lb = urwid.ListBox(self.walker)
        urwid.WidgetWrap.__init__(self, self.lb)

    def modified(self):
        focus_w, _ = self.walker.get_focus()
        urwid.emit_signal(self, 'show_details', focus_w.data, [])

    def set_data(self, outlets):
        outlets_widgets = [ListItem(o) for o in outlets]
        urwid.disconnect_signal(self.walker, 'modified', self.modified)

        while len(self.walker) > 0:
            w = self.walker.pop()
            urwid.disconnect_signal(w, "item_activated", self.item_activated)
        self.walker.extend(outlets_widgets)

        urwid.connect_signal(self.walker, "modified", self.modified)
        for w in outlets_widgets:
            urwid.connect_signal(w, "item_activated", self.item_activated)

        self.walker.set_focus(0)
   
    # throw up
    def item_activated(self, item):
        urwid.emit_signal(self, 'item_activated', 1, [])


class PowerStripController(object):
    cfg = None
    last_refresh = None
    multi_power_on_delay = 2

    def __init__(self, cfg):
        self.cfg = cfg
        self.outlets = []
        True

    def get_last_refresh(self):
        return self.last_refresh

    def _apply_on_state(self, outlets, outlet_id):
        self.outlets[outlet_id-1]['state'] = 1
        self.outlets[outlet_id-1]['last_on'] = datetime.now()

    def _apply_off_state(self, outlets, outlet_id):
        self.outlets[outlet_id-1]['state'] = 0
        self.outlets[outlet_id-1]['last_off'] = datetime.now()


class IPMISessionKeepaliveThread(threading.Thread):
    
    def __init__(self, stop_event):
        self.stop_event = stop_event


    def run(self):
        Command.eventloop()

    

class IPMIDevice(PowerStripController):

    timeout = 3000
    cmd = None
    power_state = 'unknown'
    last_session_usage = 0

    def __init__(self, cfg):
        super(IPMIDevice, self).__init__(cfg)
        
        self.cmd = self.get_cmd()

    def get_cmd(self):
        # create new ipmi session to prevent running into timeouts
        #if not self.last_session_usage == 0:
        #    print(time.time() - self.last_session_usage)
        #if self.cmd == None or time.time() - self.last_session_usage > 20:
        cmd = None
        if self.cmd == None:
            try:
                port = 623
                if 'port' in self.cfg:
                    port = int(self.cfg['port'])
                cmd = ipmi_command.Command(
                    bmc=self.cfg['host'], userid=self.cfg['user'], password=self.cfg['pwd'], port=port
                )
                self.cmd = cmd
                self.event_thread = IPMISessionKeepaliveThread(None)
                self.event_thread.start()
             
            except Exception as e:
                #urwid.ExitMainLoop()
                print(e)

        #self.last_session_usage = time.time()
        return self.cmd

    def refresh_status(self):
        # state
        state = self.get_cmd().get_power()
        iState = 0
        if "on" in state['powerstate']:
            iState = 1
        
        # bootdev
        bootdev = self.get_cmd().get_bootdev()
        bootdevstr = 'bootdev: ' + bootdev['bootdev']
        if bootdev['persistent']:
            bootdevstr += ', persistent'
        else:
            bootdevstr += ', temporary'

        # sensor data
        #sensor_data = {}
        #for x in self.get_cmd().get_sensor_data():
        #    sensor_data['name'] = x.name
        #    sensor_data['value'] = x.value
        #    sensor_data['unit'] = x.units
        #    sensor_data['health'] = x.health
        
        outlets =  [{
            'name': self.cfg.name,
            'state': iState,
            'preset1': 0,
            'preset2': 0,
            'preset3': 0,
            'bootdev': bootdevstr,
        #        'sensor_data': sensor_data
        }]
        if len(self.outlets) == 0:
            self.outlets = outlets
        else:
            for key in outlets[0]:
                self.outlets[0][key] = outlets[0][key] 
        
        #self.outlets = outlets



    def get_event_log():
        return self.get_cmd().get_event_log()

    def _switch(self, state):
        self.get_cmd().set_power(state, wait=2000)

    def switch_on(self, outlet_id):
        self._switch("on")
        self._apply_on_state(self.outlets, outlet_id)

    def switch_off(self, outlet_id):
        self._switch("off")
        self._apply_off_state(self.outlets, outlet_id)

    def toggle_outlet(self, outlet_id):
        if self.outlets[outlet_id-1]['state'] == 0:
            self.switch_on(outlet_id)
        else:
            self.switch_off(outlet_id)

class RedfishDevice(PowerStripController):

    timeout = 3000
    cmd = None

    def __init__(self, cfg):
        super(RedfishDevice, self).__init__(cfg)

        power_state = 'unknown'
        try:
            port = 623
            if 'port' in self.cfg:
                port = int(self.cfg['port'])
            self.cmd = redfish_command.Command(
                bmc=self.cfg['host'], userid=self.cfg['user'], password=self.cfg['pwd'], port=port, verifycallback=self.verify_callback
            )
            #print(self.cmd)
        except Exception as e:
            print(e)

    def verify_callback(self, x):
        # ssl?!
        return True

    def get_power_state(self):
        return self.cmd.get_power()

    def refresh_status(self):
        state = self.cmd.get_power()

        # bootdev
        bootdev = self.cmd.get_bootdev()
        bootdevstr = 'bootdev: ' + bootdev['bootdev']
        if bootdev['persistent']:
            bootdevstr += ', persistent'
        else:
            bootdevstr += ', temporary'

        outlet = {
            'name': self.cfg.name,
            'state': 0,
            'preset1': 0,
            'preset2': 0,
            'preset3': 0,
            'bootdev': bootdevstr
        }
        if len(self.outlets) == 1:
            for key in outlet:
                self.outlets[0][key] = outlet[key]
        else:
            self.outlets.append(outlet)

    def _switch(self, state):
        self.cmd.set_power(state, wait=2000)

    def switch_on(self, outlet_id):
        self._switch("on")
        self._apply_on_state(self.outlets, outlet_id)

    def switch_off(self, outlet_id):
        self._switch("off")
        self._apply_off_state(self.outlets, outlet_id)

    def toggle_outlet(self, outlet_id):
        if self.outlets[outlet_id-1]['state'] == 0:
            self.switch_on(outlet_id)
        else:
            self.switch_off(outlet_id)
        


'''
Power over Ethernet Power Sourcing Equipment
Uses standard SNMP OIDs and should be compatible with most PoE devices with SNMP
'''
class PoEPSE(PowerStripController):
  
    oids = {
        # single values
        'sysName': ObjectIdentity('.1.3.6.1.2.1.1.5.0'),
    }
    bulk_cmd_oids = {
	# start oids
        # pethPsePortAdminEnable: .1.3.6.1.2.1.105.1.1.1.3
        'ifAlias': ObjectIdentity('.1.3.6.1.2.1.31.1.1.1.18'),
        'ifAdminStatus': ObjectIdentity('.1.3.6.1.2.1.2.2.1.7'),
        'ifOperStatus': ObjectIdentity('.1.3.6.1.2.1.2.2.1.8'),
        'ifMtu': ObjectIdentity('.1.3.6.1.2.1.2.2.1.4'),
        'ifJackType': ObjectIdentity('.1.3.6.1.2.1.26.2.2.1.2'),
        'pethPsePortAdminEnable': ObjectIdentity('.1.3.6.1.2.1.105.1.1.1.3.1'),
        'macAddresses': ObjectIdentity('.1.3.6.1.2.1.17.4.3.1.2'),
    }
    # key is bulk_cmd_oids key
    bulk_cmds = {}

    # for received data. key is same as in bulk_cmd_oids, value is an array with the value for each outlet
    data = {}
    mac_addresses = {}

    def __init__(self, cfg):
        super(PoEPSE, self).__init__(cfg)
        self._configure_connection()

    # pull into snmp client super class
    def _get_bulk_cmd(self, first_oid_key):
        return bulkCmd(self.snmpEngine, self.userData, self.transport, ContextData(), 0, 7, self._getObjectType(first_oid_key))

    def _get_mac_addresses(self):
        self.mac_addresses.clear()
        for i in range(0, 8):
            self._parse_mac_addresses(self.bulkCmdMacAddresses)

    def oid2mac(self, oid):
        return "%0.2X:%0.2X:%0.2X:%0.2X:%0.2X:%0.2X" % (int(oid[0]), int(oid[1]), int(oid[2]), int(oid[3]), int(oid[4]), int(oid[5]))

    # pull into snmp client super class
    def _configure_connection(self):
        self.snmpEngine = SnmpEngine()
        authProtocol = usmHMACMD5AuthProtocol
        privProtocol = usmAesCfb128Protocol
        if self.cfg['auth_protocol'] == 'SHA':
            authProtocol = usmHMACSHAAuthProtocol
        if self.cfg['priv_protocol'] == 'DES':
            privProtocol = usmDESPrivProtocol
        self.userData = UsmUserData(self.cfg['user'], self.cfg['authkey'], self.cfg['privkey'], authProtocol=authProtocol, privProtocol=privProtocol)
        port = 161
        if not self.cfg['port'] == None:
            port = int(self.cfg['port'])
        self.transport = UdpTransportTarget((self.cfg['host'], port), timeout=0.5, retries=1)

    # pull into snmp client super class
    def _getObjectType(self, oidKey):
         if oidKey in self.oids:
	         objectType = ObjectType(self.oids[oidKey]) #.addAsn1MibSource( 'file:///home/gobuki/bin/snmp/PE_MIB_1.1.115')
         else:
	         objectType = ObjectType(self.bulk_cmd_oids[oidKey]) #.addAsn1MibSource( 'file:///home/gobuki/bin/snmp/PE_MIB_1.1.115')
         return objectType

    def _parse_mac_addresses(self, iterator):
        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
        if errorIndication:
            print(errorIndication)
        elif errorStatus:
            print('%s at %s' % (errorStatus.prettyPrint(),
                        errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
        else:
            for varBind in varBinds:
                # cut off the prefix, so only 6 values separated by dots should remain
                mac_as_oid = str(varBind[0]).replace('1.3.6.1.2.1.17.4.3.1.2.','')
                split_oid = mac_as_oid.split('.')
                if len(split_oid) == 6:
                    iPort = varBind[1]
                    if iPort <= 8:
                        if not str(iPort) in self.mac_addresses:
                            self.mac_addresses[str(iPort)] = [self.oid2mac(split_oid)]
                        else:
                            self.mac_addresses[str(iPort)].append(self.oid2mac(split_oid))
                        #print(self.oid2mac(split_oid) + " found at port " + str(varBind[1]))

    def _append_result(self, iterator, listvar):
        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
        if errorIndication:
            print(errorIndication)
        elif errorStatus:
            print('%s at %s' % (errorStatus.prettyPrint(),
                        errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
        else:
            for varBind in varBinds:
                listvar.append(str(varBind[1]))

    def refresh_status(self):
        for key in self.bulk_cmd_oids:
            self.bulk_cmds[key] = self._get_bulk_cmd(key)
            if key not in self.data:
                self.data[key] = []
        for key in self.data:
            self.data[key].clear()

        self.bulkCmdMacAddresses = bulkCmd(self.snmpEngine, self.userData, self.transport, ContextData(), 0, 7, self._getObjectType('macAddresses'))
        self._get_mac_addresses()
 
        for i in range(0, 8):
            for key in self.bulk_cmds:
                self._append_result(self.bulk_cmds[key], self.data[key])

            self.data["pethPsePortAdminEnable"][i] = int(self.data["pethPsePortAdminEnable"][i])

            itype = int(self.data['ifJackType'][i])
            stype = ""
            slink = ""
            link_status = int(self.data['ifOperStatus'][i])
           
            if link_status == 1:
                slink = "up"
            elif link_status == 2:
                slink = "down"

            if itype == 2:
                stype = "RJ45 PoE, " + slink
            else:
                stype = '<unsupported>'
 
            mac_addrs = []
            if str(i+1) in self.mac_addresses:
                mac_addrs = self.mac_addresses[str(i+1)]

            outlet = { 
                    'name': self.data['ifAlias'][i],
                    'state': int(self.data['pethPsePortAdminEnable'][i]) - 1,
                    'preset1': 0,
                    'preset2': 0,
                    'preset3': 0,
                    'type': stype,
                    'mac_addrs': mac_addrs
            }
            if i < len(self.outlets):
                for key in outlet:
                    self.outlets[i][key] = outlet[key]
            else:
                self.outlets.append(outlet)
        

    def switch_on(self, outlet_id):
        self._switch(outlet_id, 2)
        self._apply_on_state(self.outlets, outlet_id)
 
    def switch_off(self, outlet_id):
        self._switch(outlet_id, 1)
        self._apply_off_state(self.outlets, outlet_id)

    def toggle_outlet(self, outlet_id):
        if self.outlets[outlet_id-1]['state'] == 0:
            self.switch_on(outlet_id)
        else:
            self.switch_off(outlet_id)

    def _switch(self, outlet_id, status):
        g = setCmd(
            self.snmpEngine,
            self.userData,
            self.transport,
            ContextData(),
            ((1, 3, 6, 1, 2, 1, 105, 1, 1, 1, 3, 1, outlet_id), rfc1902.Integer(status))
        )
        next(g)

'''
Controls ATEN PDUs using SNMP. Support is specific to ATEN devices.
I developed it for PE8108G, but it should be compatible with PE8104G for example and mabye even others
'''
class AtenPDU(PowerStripController):

    oids = {                                                                               
        # single values
        'sysName': ObjectIdentity('.1.3.6.1.2.1.1.5.0'),
        'modelName': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.1.1.0'),
        'uptime': ObjectIdentity('.1.3.6.1.2.1.1.3.0'),
        'time': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.3.4.8.2.2.0'),
        'date': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.3.4.8.2.1.0'),
        'deviceMAC': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.3.4.1.0'),
        'deviceIP': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.3.4.2.0'),
        'deviceFWVersion': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.3.4.3.0'),
        'devicePower': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.1.3.1.4.1'),
        'devicePowerDissipation': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.1.3.1.5.1'),
        'deviceVoltage': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.1.3.1.3.1'),
        'deviceCurrent': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.1.3.1.2.1'),
        'inputMaxVoltage': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.1.3.1.6.1'),
        'inputMaxCurrent': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.1.3.1.7.1'),
    }

    # start oids
    bulk_cmd_oids = {                                                                               
        'outletName': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.10.1.2'),
        'displayOutletStatus': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.1.5.1.2'),
        'outletVoltage': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.1.1.3'),
        'outletCurrent': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.1.1.2'),
        'outletPower': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.1.1.4'),
        'outletPowerDissipation': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.1.1.5'),
        'outletValueEntry': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.1'),
        'deviceValueEntry': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.1'),
        'outletOnDelayTime': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.10.1.4'),
        'outletOffDelayTime': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.10.1.5'),
        'outletMaxCurrent': ObjectIdentity('.1.3.6.1.4.1.21317.1.3.2.2.2.2.1.1.6')
    }
    # key is bulk_cmd_oids key
    bulk_cmds = {}

    # for received data
    data = {}

    def __init__(self, cfg):
        super(AtenPDU, self).__init__(cfg)
        self._configure_connection()

    def _get_bulk_cmd(self, first_oid_key):
        return bulkCmd(self.snmpEngine, self.userData, self.transport, ContextData(), 0, 7, self._getObjectType(first_oid_key))

    def _configure_connection(self):
        self.snmpEngine = SnmpEngine()
        authProtocol = usmHMACMD5AuthProtocol
        privProtocol = usmAesCfb128Protocol
        if self.cfg['auth_protocol'] == 'SHA':
            authProtocol = usmHMACSHAAuthProtocol
        if self.cfg['priv_protocol'] == 'DES':
            privProtocol = usmDESPrivProtocol
        self.userData = UsmUserData(self.cfg['user'], self.cfg['authkey'], self.cfg['privkey'], authProtocol=authProtocol, privProtocol=privProtocol)
        #self.userData = UsmUserData(self.cfg['user'], self.cfg['authkey'], self.cfg['privkey'], authProtocol=usmHMACMD5AuthProtocol, privProtocol=usmAesCfb128Protocol)
        port = 161
        if not self.cfg['port'] == None:
            port = int(self.cfg['port'])
        self.transport = UdpTransportTarget((self.cfg['host'], port), timeout=0.5, retries=1)

    def _getObjectType(self, oidKey):
         if oidKey in self.oids:
	         objectType = ObjectType(self.oids[oidKey]) #.addAsn1MibSource( 'file:///home/gobuki/bin/snmp/PE_MIB_1.1.115')
         else:
	         objectType = ObjectType(self.bulk_cmd_oids[oidKey]) #.addAsn1MibSource( 'file:///home/gobuki/bin/snmp/PE_MIB_1.1.115')
         return objectType

    def _append_result(self, iterator, listvar):                                               
        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
        if errorIndication:
            print(errorIndication)
        elif errorStatus:
            print('%s at %s' % (errorStatus.prettyPrint(),
                        errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
        else:
            for varBind in varBinds:
                listvar.append(str(varBind[1]))

    def get_result(self, iterator):
        listvar = []
        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
        if errorIndication:
            print(errorIndication)
        elif errorStatus:
            print('%s at %s' % (errorStatus.prettyPrint(),
                        errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
        else:
            for varBind in varBinds:
                listvar.append(str(varBind[1]))
        return listvar
                

    def get_pdu_info(self):
        return self.get_result(
            self.getGetCmd(['sysName','modelName','uptime','time','date','deviceMAC','deviceIP','deviceFWVersion','deviceVoltage','deviceCurrent','devicePower','devicePowerDissipation'])
        )

    def switch_on(self, outlet_id):
        self._switch(outlet_id, 2)
        self._apply_on_state(self.outlets, outlet_id)
 
    def switch_off(self, outlet_id):
        self._switch(outlet_id, 1)
        self._apply_off_state(self.outlets, outlet_id)

    def toggle_outlet(self, outlet_id):
        if self.outlets[outlet_id-1]['state'] != 1:
            self.switch_on(outlet_id)
        else:
            self.switch_off(outlet_id)

    def _switch(self, outlet_id, status):
        g = setCmd(
            self.snmpEngine,
            self.userData,
            self.transport,
            ContextData(),
            ((1, 3, 6, 1, 4, 1, 21317, 1, 3, 2, 2, 2, 2, outlet_id + 1, 0), rfc1902.Integer(status))
        )
        next(g)

    def refresh_status(self):
        for key in self.bulk_cmd_oids:
            self.bulk_cmds[key] = self._get_bulk_cmd(key)
            if key not in self.data:
                self.data[key] = []
        for key in self.data:
            self.data[key].clear()

        for i in range(0, 8):
            for key in self.bulk_cmds:
                self._append_result(self.bulk_cmds[key], self.data[key])
                if key == "displayOutletStatus":
                    self.data[key][i] = int(self.data[key][i])
            outlet = { 
                    'name': self.data['outletName'][i],
                    'state': int(self.data['displayOutletStatus'][i]) - 1,
                    'preset1': 0,
                    'preset2': 0,
                    'preset3': 0,
                    'power': self.data['outletPower'][i],
                    'powerDissipation': self.data['outletPowerDissipation'][i],
                    'current': self.data['outletCurrent'][i],
                    'max_current': self.data['outletMaxCurrent'][i],
                    'voltage': self.data['outletVoltage'][i],
                    'on_delay': self.data['outletOnDelayTime'][i],
                    'off_delay': self.data['outletOffDelayTime'][i],
            }
            if i < len(self.outlets):
                for key in outlet:
                    self.outlets[i][key] = outlet[key]
            else:
                self.outlets.append(outlet)

    def getGetCmd(self, oidKey):
        if isinstance(oidKey, str):
            return getCmd(self.snmpEngine, self.userData, self.transport, ContextData(), self._getObjectType(oidKey))
        else:
            object_types = [self._getObjectType(key) for key in oidKey]
            return getCmd(self.snmpEngine, self.userData, self.transport, ContextData(), *object_types)
 
           

class SignalWrap(urwid.WidgetWrap):                          

    def __init__(self, w, is_preemptive=False):
        urwid.WidgetWrap.__init__(self, w)
        self.event_listeners = []
        self.is_preemptive = is_preemptive

    def listen(self, mask, handler):
        self.event_listeners.append((mask, handler))

    def keypress(self, size, key):
        result = key

        if self.is_preemptive:
            for mask, handler in self.event_listeners:
                if mask is None or mask == key:
                    result = handler(self, size, key)
                    break

        if result is not None:
            result = self._w.keypress(size, key)

        if result is not None and not self.is_preemptive:
            for mask, handler in self.event_listeners:
                if mask is None or mask == key:
                    return handler(self, size, key)

        return result


'''
Controls Anel NET-PwrCtrl powerstrips
'''
class NetPwrCtrl(PowerStripController):
    def __init__(self, cfg):
        super(NetPwrCtrl, self).__init__(cfg)

    def is_outlet_configured(self, outlet_index): 
        is_configured = False
        # try to get outlet name from config, could be unconfigured
        # in this case access to cfg entry fails in if condition
        try:
            is_configured = not self.cfg[str(outlet_index)] == None
        except Exception as e:
            print(e)

        return is_configured

    def refresh_status(self):
        outlet_data = self._fetch_outlet_states()
        outlet_index = 1
        for od in outlet_data:
            if self.is_outlet_configured(outlet_index):
                name = od[1]
                outlet = { 
                    'name': self.cfg[str(outlet_index)],
                    'state': int(od[1]),
                    'preset1': 0,
                    'preset2': 0,
                    'preset3': 0,
      
                }
                if outlet_index-1 < len(self.outlets):
                    for key in outlet:
                        self.outlets[outlet_index-1][key] = outlet[key]
                else:
                    self.outlets.append(outlet)
               

            outlet_index += 1
        
    def _fetch_outlet_states(self):
        h = httplib2.Http()
        h.add_credentials(self.cfg['user'], self.cfg['pwd'])
        (resp_headers, content) = h.request("http://" + self.cfg['host'] + "/?Stat=" + self.cfg['user'] + self.cfg['pwd'], "GET")
        values = content.decode().split(';')
        # the first 8 fields of the array can be ignored for the state 
        # from index 8 (field 9) on the socket states are found,
	# with 3 fields per socket: name, state integer, another integer
        
        i = 8
        outlet_data = []
        while (i + 2 < len(values)):
            outlet_data.append([values[i], values[i+1], values[i+2]])
            i+=3

        self.last_refresh = values[3]
        return outlet_data;

    def _switch(self, outlet_id, command):
        s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        s.sendto((command + str(outlet_id) + self.cfg['user'] + self.cfg['pwd'] +"\n").encode(), (self.cfg['host'], int(self.cfg['port'])))

    def switch_on(self, outlet_id):
        self._switch(outlet_id, "Sw_on")
        self._apply_on_state(self.outlets, outlet_id)

    def switch_off(self, outlet_id):
        self._switch(outlet_id, "Sw_off")
        self._apply_off_state(self.outlets, outlet_id)

    def toggle_outlet(self, outlet_id):
        if self.outlets[outlet_id-1]['state'] == 0:
            self.switch_on(outlet_id)
        else:
            self.switch_off(outlet_id)

    def activate_preset(self, preset_index):
        outlet_id = 1
        outlets_switched_on = 0

        for outlet in self.outlets:
            preset_value = outlet['preset'+ str(preset_index)]

            if preset_value != outlet['state']:
                # sleep before toggling the next outlet
                if outlets_switched_on > 0:
                    time.sleep(self.multi_power_on_delay)
                self.toggle_outlet(outlet_id)
                if preset_value == 1:
                   outlets_switched_on += 1
            outlet_id += 1


class OutletDetailView(urwid.WidgetWrap):
    def __init__ (self):
        t = urwid.Text("")
        urwid.WidgetWrap.__init__(self, t)
    def set_outlet(self, o):

        s = f'Name: {o["name"]}\n'
        if 'power' in o:
           s += f'Power:  {o["power"]}\n'
        if 'current' in o:
           s += f'Current:  {o["current"]}'
        if 'sensor_data' in o:
           s += self.format_sensor_data(o['sensor_data'])
        self._w.set_text(s)

    def format_sensor_data(self, sensor_data):
        if not sensor_data:
            return ""
        print(sensor_data)


class CursesUI:

    # activated powerstrip config index
    selected_powerstrip = 0

    # checkbox lists
    content = []
    preset1_content = []
    preset2_content = []
    preset3_content = []

    # Set up color scheme
    palette = [
        ('titlebar', 'light green', ''),
        ('hotkey', 'dark green,bold', ''),
        ('quit', 'dark red,bold', ''),
        ('quit button', 'dark red', ''),
        ('headers', 'white,bold', ''),
        ('outlets_header', 'light blue', ''),
        ('outlet_selected', 'black', 'light green'),
        ('button', 'white', 'light blue'),
        ('button_selected', 'black', 'yellow'),
        ('normal', 'white', ''),
        ('selected', 'black', 'light green')]

    instances = {}

    def __init__(self):
        self.cfg = ConfigManager()
        self.quit_event_loop = False

        if self.cfg.config_exists():
            self.load_config(self.selected_powerstrip)

    def load_config(self, selected_powerstrip_index):
        cfg_sectionname = self.cfg.get_sections()[selected_powerstrip_index]
        cfg_section = self.cfg.get_section(cfg_sectionname)
        self.load_controller_instance(cfg_section)
    
    def load_controller_instance(self, cfg_section):
        if cfg_section['device'] == "anel_powerstrip":
            if not cfg_section.name in self.instances:
                self.instances[cfg_section.name] = NetPwrCtrl(cfg_section)
            self.active_powerstrip = self.instances[cfg_section.name]
        elif cfg_section['device'] == "aten_pdu":
            if not cfg_section.name in self.instances:
                self.instances[cfg_section.name] = AtenPDU(cfg_section)
            self.active_powerstrip = self.instances[cfg_section.name]
        elif cfg_section['device'] == "poe_pse": 
            if not cfg_section.name in self.instances:
                self.instances[cfg_section.name] = PoEPSE(cfg_section)
            self.active_powerstrip = self.instances[cfg_section.name]
        elif cfg_section['device'] == "ipmi":
            if not cfg_section.name in self.instances:
                self.instances[cfg_section.name] = IPMIDevice(cfg_section)
            self.active_powerstrip = self.instances[cfg_section.name]
        elif cfg_section['device'] == "redfish":
            if not cfg_section.name in self.instances:
                self.instances[cfg_section.name] = RedfishDevice(cfg_section)
            self.active_powerstrip = self.instances[cfg_section.name]

          

    def next_powerstrip(self, w, size, key):
        next_index = self.selected_powerstrip + 1
        if next_index > len(self.cfg.get_sections())-1:
            next_index = 0
        try:
            #print("loading config", file=sys.stderr)
            self.load_config(next_index)
            #print ("after loading config", file=sys.stderr)
        except:
            print("exception", file=sys.stderr)
            return
         
        #print("after loading config", file=sys.stderr)
        self.selected_powerstrip = next_index
        self.refresh_ui(keep_selection=False)
        self.title.set_text(self.active_powerstrip.cfg.name + u' ' + self.active_powerstrip.cfg['host'] + ':' + self.active_powerstrip.cfg['port'])
             
    def previous_powerstrip(self, w, size, key):
        prev_index = self.selected_powerstrip - 1
        if prev_index < 0:
            prev_index = len(self.cfg.get_sections()) - 1

        self.selected_powerstrip = prev_index
        self.load_config(self.selected_powerstrip)
        self.refresh_ui(keep_selection=False)
        self.title.set_text(self.active_powerstrip.cfg.name + u' ' + self.active_powerstrip.cfg['host'] + ':' + self.active_powerstrip.cfg['port'])

    def load_preset_config(self):
        self.preset1_content.clear()
        self.preset2_content.clear()
        self.preset3_content.clear()
        self.preset1_content.append(urwid.AttrMap(self.preset1_button, "normal", "selected"))
        self.preset1_content.append(urwid.Text(""))
        self.preset2_content.append(urwid.AttrMap(self.preset2_button, "normal", "selected"))
        self.preset2_content.append(urwid.Text(""))
        self.preset3_content.append(urwid.AttrMap(self.preset3_button, "normal", "selected"))
        self.preset3_content.append(urwid.Text(""))
         
        for outlet in self.active_powerstrip.outlets:
            if 'preset1' in self.active_powerstrip.cfg:
                cb_preset1 = urwid.CheckBox(outlet['name'], outlet['preset1'])
                self.preset1_content.append(urwid.AttrMap(cb_preset1, "normal", "selected"))
            if 'preset2' in self.active_powerstrip.cfg:
                cb_preset2 = urwid.CheckBox(outlet['name'], outlet['preset2'])
                self.preset2_content.append(urwid.AttrMap(cb_preset2, "normal", "selected"))
            if 'preset3' in self.active_powerstrip.cfg:
                cb_preset3 = urwid.CheckBox(outlet['name'], outlet['preset3'])
                self.preset3_content.append(urwid.AttrMap(cb_preset3, "normal", "selected"))

        # for some devices it does not make much sense to configure grouped presets
        # only display presets, when presets are configured to save 50% of the screen space for other stuff
        if not 'preset1' in self.active_powerstrip.cfg and not 'preset2' in self.active_powerstrip.cfg and not 'preset3' in self.active_powerstrip.cfg:
            del self.body_pile.contents[1]
        #    self.preset_view = self.body_pile.contents[1]
        #    if self.preset_view in self.body_pile.contents:
        #        self.body_pile.contents.remove(self.preset_view)


    def refresh_ui(self, keep_selection=True):
        pos = None
        if keep_selection:
            try:
                pos = self.outlets_listview.lb.focus_position
            except:
                True

        self.active_powerstrip.refresh_status()

        self.content.clear()

        for outlet in self.active_powerstrip.outlets:
            # main checkbox
            if 'type' in outlet:
                name = outlet['name'] + ' ' + outlet['type']
            else:
                name = outlet['name']

            if 'voltage' in outlet:
                name += ' (' + outlet['voltage'] + 'V'
            if 'current' in outlet:
                name += ', ' + outlet['current'] + 'A'
            if 'power' in outlet:
                name += ', ' + outlet['power'] + 'W)'
            if 'mac_addrs' in outlet:
                if len(outlet['mac_addrs']) > 0:
                    name += ' ' + outlet['mac_addrs'][0]
                if len(outlet['mac_addrs']) > 1:
                    name += ' ' + outlet['mac_addrs'][1]
                if len(outlet['mac_addrs']) > 2:
                    name += ' ' + outlet['mac_addrs'][2]
            if 'bootdev' in outlet:
                    name += ' ' + outlet['bootdev']

            # ATEN PDU uses 2 as on, checkbox 1 for checked
            state = outlet['state']
            if state == 2:
                state = 1

            cb = urwid.CheckBox(name, state, on_state_change=self.on_checkbox_toggled)

            # checkboxes for preset visualization (have no handler)
            self.content.append(urwid.AttrMap(cb, "normal", "selected"))

        self.outlets_listview.set_data(self.active_powerstrip.outlets)

        self.listview_header.set_text(self.get_outlets_listview_header())

        if keep_selection:
            if pos is not None and len(self.active_powerstrip.outlets) > 0:
                self.outlets_listview.lb.set_focus(pos)

    def get_outlets_listview_header(self):
        text = "Name                State"
        o = self.active_powerstrip.outlets[0]
        #if 'last_on' in o:
        #    text += '{:>12s}'.format("Last on")
        #if 'last_off' in o:
        #    text += '{:>12s}'.format("Last off")
        if 'on_delay' in o:
            text += "  On Delay"
        if 'off_delay' in o:
            text += "  Off Delay"
        if 'voltage' in o:
            text += "       V"
        if 'current' in o:
            text += "       A"
        if 'power' in o:
            text += "       W"
        if 'powerDissipation' in o:
            text += "     KWh"
        if 'type' in o:
            text += "  Type"
        if 'mac_addrs' in o:
            text += "           MACs seen at port"
        if 'bootdev' in o:
            text += "  Boot Device"
        return text

    def create_device_listview(self):
        device_listview = ListView()
        header = urwid.AttrWrap(urwid.Text("Name         State"), "outlets_header", None)
        device_linebox = urwid.LineBox(urwid.Frame(header=header, body=device_listview), title="Devices")
        return device_linebox

    def create_outlets_listview(self):
        self.outlets_listview = ListView()
        urwid.connect_signal(self.outlets_listview, "item_activated", self.toggle_selected_outlet_by_click)
        self.listview_header = urwid.AttrWrap(urwid.Text("Name         State"), "outlets_header", None)
        self.outlets_linebox = urwid.LineBox(urwid.Frame(header=self.listview_header, body=self.outlets_listview), title="Outlets")
        return self.outlets_linebox

    def create_outlet_detail_view(self):
        self.outlet_detail_view = OutletDetailView()
        self.details_content = urwid.Filler(self.outlet_detail_view, valign="top")
        self.details_linebox = urwid.LineBox(self.details_content, title="Outlet Details")
        return self.details_linebox

    def create_device_presets_view(self):
        self.preset1_content = urwid.SimpleListWalker([])
        self.preset1_button = urwid.Button("Activate", on_press = self.activate_preset1)
        self.preset2_content = urwid.SimpleListWalker([])
        self.preset2_button = urwid.Button("Activate", on_press = self.activate_preset2)
        self.preset3_content = urwid.SimpleListWalker([])
        self.preset3_button = urwid.Button("Activate", on_press = self.activate_preset3)
        self.outlets_listbox = urwid.ListBox(self.content)
        self.preset1_listbox = urwid.ListBox(self.preset1_content)
        self.preset2_listbox = urwid.ListBox(self.preset2_content)
        self.preset3_listbox = urwid.ListBox(self.preset3_content)

        #self.preset_cols = urwid.Columns([self.preset1_listbox, self.preset2_listbox, self.preset3_listbox])
        #self.presets_linebox = urwid.LineBox(self.preset_cols, title="Presets")
        self.preset1_linebox = urwid.LineBox(self.preset1_listbox, title="Preset 1")
        self.preset2_linebox = urwid.LineBox(self.preset2_listbox, title="Preset 2")
        self.preset3_linebox = urwid.LineBox(self.preset3_listbox, title="Preset 3")
        self.presets_columns = urwid.Columns([self.preset1_linebox, self.preset2_linebox, self.preset3_linebox])

        return self.presets_columns

    def create_main_menu(self):
        # Create the menu
        menu = urwid.Text([
            u'(', ('hotkey', u'r'), u') refresh  ',
            u'(', ('hotkey', u'e'), u') edit  ',
            u'(', ('hotkey', u'enter'), u') toggle power  ',
            u'(', ('hotkey', u'p'), u') previous PDU  ',
            u'(', ('hotkey', u'n'), u') next PDU  ',
            u'(', ('quit', u'q'), u') quit'
        ])
        return menu

    def init_ui(self):
    
        header_text = self.active_powerstrip.cfg.name + u' ' + self.active_powerstrip.cfg['host'] + ':' + self.active_powerstrip.cfg['port']
        if not self.active_powerstrip.get_last_refresh() == None:
            header_test += ' ' + self.active_powerstrip.get_last_refresh()
        self.title = urwid.Text(header_text)
        header = urwid.AttrMap(self.title, 'titlebar')
        self.content = urwid.SimpleListWalker([])
    
        #bodypile = urwid.Pile([self.outlets_listbox, urwid.Text(u'Foo')])

        #left_col_pile = urwid.Pile([self.create_device_listview(), self.create_outlets_listview()])
        left_col_pile = urwid.Pile([self.create_outlets_listview()])

        self.body_pile_content = [urwid.Columns([left_col_pile, self.create_outlet_detail_view()]), self.create_device_presets_view()]
        self.body_pile = urwid.Pile(self.body_pile_content)

        self.layout = urwid.Frame(header=urwid.Columns([header, urwid.Edit(caption="Multi Power on Delay: ", edit_text=str(self.active_powerstrip.multi_power_on_delay))]), body=self.body_pile, footer=self.create_main_menu())

        if not self.cfg.config_exists():
            print("no config")
            config = self.cfg.init()

        self.main_loop = urwid.MainLoop(self.layout, self.palette, unhandled_input=self.handle_input)
        urwid.connect_signal(self.outlets_listview, "show_details", self.show_details)
        self.refresh_ui() 
        self.load_preset_config()

    def show_details(self, outlet, foo):
        self.outlet_detail_view.set_outlet(outlet)
 
    # Handle key presses
    def handle_input(self, key):
        if key == 'R' or key == 'r':
           self.refresh_ui()
        elif key == 'Q' or key == 'q':
            raise urwid.ExitMainLoop()
        elif key == 'e':
            self.handle_edit_powerstrip_key(None, None, None)
        elif key == 'r':
            self.handle_reload_key(None, None, None)
        elif key == 'p':
            self.previous_powerstrip(None, None, None)
        elif key == 'n':
            self.next_powerstrip(None, None, None)
        elif key == 'enter':
            self.toggle_selected_outlet()
        elif key == 'tab':
           if self.layout.get_focus() == 'body':
               self.layout.focus_position = 'header'
           elif self.layout.get_focus() == 'header':
               self.layout.focus_position = 'body'
        else:
             try:
                  outlet_id = int(key)
                  if 0 < outlet_id < 9:
                      self.netpwrctrl.toggle_outlet(outlet_id);
                      self.refresh_ui()
             except:
                  True
                       
        #self.top.listen('r', self.handle_reload_key)
        #self.top.listen('n', self.next_powerstrip)
        #self.top.listen('p', self.previous_powerstrip)
        #self.top.listen('tab', self.toggle_ui_focus)
        
    def quit(self, w, size, key):
        #self.screen.stop()
        #self.quit_event_loop = True
        urwid.ExitMainLoop()
        sys.exit(0)

    def toggle_ui_focus(self, w, size, key):
       if self.layout.get_focus() == 'body':
           self.layout.focus_position = 'header'
       elif self.layout.get_focus() == 'header':
           self.layout.focus_position = 'body'

    def edit_powerstrip(self, powerstrip=None):
        d = self.open_edit_powerstrip_dialog(powerstrip)
        if d == None:
            return
        name = d['name']
        if name == None:
            return
        host = d['host']
        if host == None:
            return
        user = d['user']
        if user == None:
            return
        pwd = d['password']
        if pwd == None:
            return
   
    def handle_reload_key(self, w, size, key):
        self.refresh_ui()

    def handle_edit_powerstrip_key(self, w, size, key):

        
        outlet = self.active_powerstrip.outlets[self.outlets_listview.lb.focus_position]
	#outlet = self.open_edit_powerstrip_dialog(outlet)

        edit_dialog = Dialog("edit_outlet", "Edit Outlet", outlet, self.layout, self.main_loop)
        edit_dialog.show()

        if outlet == None:
            return

        name = outlet['name']
        if name == None:
            name = ""


        
        # TODO: save result !

        self.refresh_ui()

    def open_edit_powerstrip_dialog(self, b):
        edit_name = urwid.Edit("Powerstrip alias: ", b['name'])
        #edit_host = urwid.Edit("host: ", b['host'])
        #edit_user = urwid.Edit("user: ", b['user'])
        #edit_pwd = urwid.Edit("password: ", '')

        #lb_contents = ([edit_name, edit_host, edit_user, edit_pwd])
        lb_contents = ([edit_name])
        lb = urwid.ListBox(urwid.SimpleListWalker(lb_contents))

        #if self.dialog(lb,         [
        #            ("OK", True),
        #            ("Cancel", False),
        #        ],
        #        title="Edit outlet"):
        #    return { 'name': b['id'], "filename": b['filename'], "position": b['position'], "rating": edit_rating.value(), "comment": edit_comment.get_edit_text() }
        

    def on_checkbox_toggled(self, arg1, arg2):
        self.toggle_selected_outlet()

    def toggle_selected_outlet_by_click(self, a, b):
        self.toggle_selected_outlet()

    def toggle_selected_outlet(self):
        outlet_id = self.outlets_listview.lb.focus_position + 1
        self.active_powerstrip.toggle_outlet(outlet_id);
        self.refresh_ui()

    def activate_preset1(self, x):
        self.active_powerstrip.activate_preset(0)
        self.refresh_ui()

    def activate_preset2(self, x):
        self.active_powerstrip.activate_preset(1)
        self.refresh_ui()

    def activate_preset3(self, x):
        self.active_powerstrip.activate_preset(2)
        self.refresh_ui()

    def _refresh(self, loop=None, user_data=None):
        self.refresh_ui()
        self.refresh_alarm = self.loop.set_alarm_in(
            self.refresh_interval_seconds, self.animate_graph)

    def _stop_refreshing(self):
        if self.refresh_alarm:
            self.loop.remove_alarm(self.refresh_alarm)
        self.refresh_alarm = None

    def run(self):
        self.init_ui()
        self.main_loop.run()
        #self.screen.start()
        #self.event_loop()

    def event_loop(self, toplevel=None):
        prev_quit_loop = self.quit_event_loop

        try:
            if toplevel is None:
                toplevel = self.top

            self.size = self.screen.get_cols_rows()

            self.quit_event_loop = False

            
            while not self.quit_event_loop:
                canvas = toplevel.render(self.size, focus=True)
                self.screen.draw_screen(self.size, canvas)
                keys = self.screen.get_input()

                for k in keys:
                    if k == "window resize":
                        self.size = self.screen.get_cols_rows()
                        #self.screen.draw_screen(self.size, canvas)
                    elif k == 'esc':
                        self.quit_event_loop = [False]
                    else:
                        toplevel.keypress(self.size, k)

            return self.quit_event_loop
        finally:
            self.quit_event_loop = prev_quit_loop

class Dialog(urwid.Frame):

    def __init__(self, dialog_type, title, data, host_view, loop=None):
        self.title = title
        self.host_view = host_view
        self.loop = loop
 
        if dialog_type == "edit_outlet":
            self.listbox = self.make_edit_outlet_dialog(data)

        self.overlay = urwid.Overlay(
            urwid.LineBox(self.listbox), host_view,
            align='center', width=('relative', 90),
            valign='middle', height=('relative', 90),
            min_width=30, min_height=12)
            
        footer = urwid.Pile([urwid.Text('Press Esc to close this dialog', align='center'), urwid.Divider()])
        urwid.Frame.__init__(self, self.overlay, footer=footer)


    def make_edit_outlet_dialog(self, data):
        blank = urwid.Divider()
        list_items = [
            urwid.Text(self.title),
            blank, urwid.Text("foo"), 
            blank, urwid.Text("bar")
        ]
        return urwid.Padding(urwid.ListBox(urwid.SimpleListWalker(list_items)), left=2, right=2)
      

    def keypress(self, size, key):
        if key == 'esc':
            self.destroy()
        else:
            return urwid.Frame.keypress(self, size, key)

    def show(self):
        if self.loop:
            self.loop.widget = self

    def destroy(self):
        if self.loop:
            self.loop.widget = self.host_view

class ConfigManager:
 
    config = None 

    def __init__(self):
        self.config = configparser.ConfigParser()
        self.configfile = expanduser('~/.netpower.ini')

        if self.config_exists:
            self.config.read(self.configfile)

    def get_sections(self):
        return self.config.sections()

    def config_exists(self):
        return exists(self.configfile)

    def get_section(self, section):
        return self.config[section]
 
    def get_first_section(self):
        return self.config[self.config.sections()[0]]

    


class Usage(Exception):
    def __init__(self, msg):
        self.msg = msg

def main(argv=None):
    if argv is None:
        argv = sys.argv
    try:
        try:
            opts, args = getopt.getopt(argv[1:], "h", ["help"])
        except getopt.error(msg):
            raise Usage(msg)
      
        if len(argv) == 1:
            app = CursesUI()
            app.run()
        elif len(argv) > 2:
            command = argv[1]
            config_section = int(argv[2])
            outlet_id = int(argv[3])
            config_manager = ConfigManager()
            section_name = config_manager.get_sections()[config_section]
            cfg = config_manager.get_section(section_name)
                
            print(cfg.name)
            ctrl = NetPwrCtrl(cfg)
            if command == 'on':
                print("Switching %s outlet %d" % (command, outlet_id))
                try:
                    ctrl.switch_on(outlet_id)
                except:
                    True
            elif command == 'off':
                print("Switching %s outlet %d" % (command, outlet_id))
                try:
                    ctrl.switch_off(outlet_id)
                except:
                    True
            else:
                print("Unknown command")
            
    except Usage as err:
        print >>sys.stderr, err.msg
        print >>sys.stderr, "for help use --help"
        return 2

if __name__ == "__main__":
    sys.exit(main())
