import sys
import getopt
import socket
#from subprocess import call
from os.path import expanduser, exists
import time
import configparser 

# for curses UI
import urwid
from wx.lib.mixins.listctrl import ColumnSorterMixin

# for SNMP with AtenPDU
from pysnmp.entity import engine, config
from pysnmp.carrier.asyncore.dgram import udp
from pysnmp.entity.rfc3413 import cmdgen
from pysnmp.proto import rfc1902

# for ANEL REST API
import httplib2


# TODO/Ideas:
# - create config file if none exists
#    - open powerstrip edit dialog, save, then reload ui
# - toggle between powerstrips (done)
# - support ATEN PDU 
#    - make NetPwrCtrl and AtenPDU inherit same subclass (done) i think it was not required and i was too much in the java world
#    - display states and names (done)
#    - toggle states
#    - display power usage
#    - edit outlet names
#    - power usage plot
# - manage crontab to send on/off commands at scheduled times (apt: python-crontab, from crontab import CronTab)
# - allow to create dependency rules
#    - switch on outlet 1 and 2, of they were off, when outlet 3 is toggled (to reduce amp popping, when mixer is powered on)


class PowerStripController(object):
    cfg = None
    last_refresh = None
    multi_power_on_delay = 2
    outlets = []

    def __init__(self, cfg):
        self.cfg = cfg
        True

    #def refresh(self):
    def get_last_refresh(self):
        return self.last_refresh

    def toggle_outlet(self, outlet_id):
        True

class AtenPDU(PowerStripController):

    namesOID  = rfc1902.ObjectName('1.3.6.1.4.1.21317.1.3.2.2.2.2.10.1.2')
    statesOID = rfc1902.ObjectName('1.3.6.1.4.1.21317.1.3.2.2.2.1.5.1.2')
    powerOID  = rfc1902.ObjectName('1.3.6.1.4.1.21317.1.3.2.2.2.2.99.1.4')

    names = []
    states = []
    power = []

    def __init__(self, cfg):
        #super(AtenPDU, self).__init__()
        super(AtenPDU, self).__init__(cfg)

        # Create SNMP engine instance
        self.snmpEngine = engine.SnmpEngine()
        self.configure_connection()

    def configure_connection(self):

        #
        # SNMPv3/USM setup
        #

        # user: usr-sha-none, auth: SHA, priv none
        config.addV3User(
            self.snmpEngine, self.cfg['user'],
            config.usmHMACMD5AuthProtocol, self.cfg['authkey'],
            config.usmAesCfb128Protocol, self.cfg['privkey']
        )
        config.addTargetParams(self.snmpEngine, "my-creds", self.cfg['user'], 'authPriv')

        #
        # Setup transport endpoint and bind it with security settings yielding
        # a target name
        #

        # UDP/IPv4
        config.addTransport(
            self.snmpEngine,
            udp.domainName,
            udp.UdpSocketTransport().openClientMode()
        )

        port = 161
        if not self.cfg['port'] == None:
            port = int(self.cfg['port'])
        config.addTargetAddr(
            self.snmpEngine, 'my-router',
            udp.domainName, (self.cfg['host'], port),
            "my-creds"
        )


    # Error/response receiver
    # noinspection PyUnusedLocal,PyUnusedLocal,PyUnusedLocal
    def cbFun(self, nmpEngine, sendRequestHandle, errorIndication,
          errorStatus, errorIndex, varBinds, cbCtx):
            if errorIndication:
                print(errorIndication)
            elif errorStatus:
                print('%s at %s' % (errorStatus.prettyPrint(),
                                    errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
            else:
                for oid, val in varBinds:
                    print('%s = %s' % (oid.prettyPrint(), val.prettyPrint()))

    def cbTableFun(self, snmpEngine, sendRequestHandle, errorIndication,
          errorStatus, errorIndex, varBindTable, cbCtx):
        if errorIndication:
            print(errorIndication)
        elif errorStatus:
            print('%s at %s' % (errorStatus.prettyPrint(),
                                errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
        else:

            #self.outlets = [{},{},{},{}, {},{},{},{}]
            first_name = True
            first_status = True
            first_power = True

            for varBindRow in varBindTable:
                for oid, val in varBindRow:
                    #print('%s = %s' % (oid.prettyPrint(), val.prettyPrint()))
                    if self.namesOID.isPrefixOf(oid):
                        #print(oid)
                        if first_name:
                            self.names.clear()
                            first_name = False
                        self.names.append(val.prettyPrint())
                    elif self.statesOID.isPrefixOf(oid):
                        if first_status:
                            self.states.clear()
                            first_status = False
                        self.states.append(val.prettyPrint())
                    elif self.powerOID.isPrefixOf(oid):
                        if first_power:
                            self.power.clear()
                            first_power = False
                        self.power.append(val.prettyPrint())

                    #else:
                    #    return False  # signal dispatcher to stop

    def bulkGet(self, initialOID):

        # Prepare and send a request message
        cmdgen.BulkCommandGenerator().sendVarBinds(
            self.snmpEngine,
            'my-router',
            None, '',  # contextEngineId, contextName
            0, 8,
            [(initialOID, None)],
            self.cbTableFun
        )

        # Run I/O dispatcher which would send pending queries and process responses
        self.snmpEngine.transportDispatcher.runDispatcher()


    def getOutletStatus(outlet):

        # Prepare and send a request message
        cmdgen.GetCommandGenerator().sendVarBinds(
            self.snmpEngine,
            'my-router',
            None, '',  # contextEngineId, contextName
            #[((1, 3, 6, 1, 2, 1, 1, 1, 0), None)],
            [((1, 3, 6, 1, 4, 1, 21317, 1, 3, 2, 2, 2, 1, 5, 1, 2, outlet), None)],
            self.cbFun
        )

        # Run I/O dispatcher which would send pending queries and process responses
        self.snmpEngine.transportDispatcher.runDispatcher()

    def load_outlet_names_and_states(self):

        self.bulkGet(self.namesOID)
        self.bulkGet(self.statesOID)
        self.bulkGet(self.powerOID)
    
        # has been quick enought for my home network, so i didn't
        # care about waiting for results yet - works for me
 
        self.outlets.clear()
        i = 0
        for name in self.names:
            outlet = { 
                    'name': self.names[i],
                    'state': int(self.states[i]) - 1,
                    'preset1': 0,
                    'preset2': 0,
                    'preset3': 0,
                    'power': self.power[i]
            }
            self.outlets.append(outlet)
            i += 1
            


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

class NetPwrCtrl(PowerStripController):
    def __init__(self, cfg):
        super(NetPwrCtrl, self).__init__(cfg)

    def is_outlet_configured(self, outlet_index): 
        is_configured = False
        # try to get outlet name from config, could be unconfigured
        # in this case access to cfg entry fails in if condition
        try:
            if not self.cfg[str(outlet_index)] == None:
                is_configured = True
        except Exception as e:
            print(e)

        return is_configured

    def load_outlet_names_and_states(self):
        self.outlets.clear()
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
                    'power': None
      
                }
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

    def toggle_outlet(self, outlet_id):
        #print(self.outlets[outlet_id-1]['state'])
        if self.outlets[outlet_id-1]['state'] == 0:
            self.switch_on(outlet_id)
        else:
            self.switch_off(outlet_id)

    def _switch(self, outlet_id, command):
        s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        s.sendto((command + str(outlet_id) + self.cfg['user'] + self.cfg['pwd'] +"\n").encode(), (self.cfg['host'], int(self.cfg['port'])))

    def switch_on(self, outlet_id):
        self._switch(outlet_id, "Sw_on")
        self.outlets[outlet_id-1]['state'] = 1

    def switch_off(self, outlet_id):
        self._switch(outlet_id, "Sw_off")
        self.outlets[outlet_id-1]['state'] = 0


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



class CursesUI:

    # activated powerstrip config index
    selected_powerstrip = 0

    # checkbox lists
    content = []
    preset1_content = []
    preset2_content = []
    preset3_content = []

    def __init__(self):
        self.cfg = ConfigManager()
        if self.cfg.config_exists():
            self.load_config()
        self.quit_event_loop = False
        self.palette = [
            ('titlebar', 'light green', ''),
            ('hotkey', 'dark green,bold', ''),
            ('quit button', 'dark red', ''),
            ('headers', 'white,bold', ''),
            ('normal', 'white', ''),
            ('button', 'white', 'light blue'),
            ('button_selected', 'black', 'yellow'),
            ('selected', 'black', 'light green')]
    

    def load_config(self):
        cfg_sectionname = self.cfg.get_sections()[self.selected_powerstrip]
        cfg_section = self.cfg.get_section(cfg_sectionname)
        #print(cfg_section)
        if cfg_section['model'] == "Anel":
            #print("loading config " + cfg_section.name)
            self.active_powerstrip = NetPwrCtrl(cfg_section)
        elif cfg_section['model'] == "ATEN":
            #print("loading config " + cfg_section.name)
            self.active_powerstrip = AtenPDU(cfg_section)

    def next_powerstrip(self, w, size, key):
        next_index = self.selected_powerstrip + 1
        if next_index > len(self.cfg.get_sections())-1:
            next_index = 0

        self.selected_powerstrip = next_index
        self.load_config()
        self.load_outlet_names_and_states()
        self.title.set_text(self.active_powerstrip.cfg.name + u' ' + self.active_powerstrip.cfg['host'] + ':' + self.active_powerstrip.cfg['port'])
             
    def previous_powerstrip(self, w, size, key):
        prev_index = self.selected_powerstrip - 1
        if prev_index < 0:
            prev_index = len(self.cfg.get_sections()) - 1

        self.selected_powerstrip = prev_index
        self.load_config()
        self.load_outlet_names_and_states()
        self.title.set_text(self.active_powerstrip.cfg.name + u' ' + self.active_powerstrip.cfg['host'] + ':' + self.active_powerstrip.cfg['port'])

    def load_outlet_names_and_states(self):
        self.active_powerstrip.load_outlet_names_and_states()
        self.content.clear()
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
            # main checkbox
            name = outlet['name']
            if not outlet['power'] == None:
                name += ' (' + outlet['power'] + 'mW)'

            cb = urwid.CheckBox(name, outlet['state'], on_state_change=self.on_checkbox_toggled)

            # checkboxes for preset visualization (have no handler)
            self.content.append(urwid.AttrMap(cb, "normal", "selected"))
            cb_preset1 = urwid.CheckBox(outlet['name'], outlet['preset1'])
            self.preset1_content.append(urwid.AttrMap(cb_preset1, "normal", "selected"))
            cb_preset2 = urwid.CheckBox(outlet['name'], outlet['preset2'])
            self.preset2_content.append(urwid.AttrMap(cb_preset2, "normal", "selected"))
            cb_preset3 = urwid.CheckBox(outlet['name'], outlet['preset3'])
            self.preset3_content.append(urwid.AttrMap(cb_preset3, "normal", "selected"))

    def init_ui(self):
    
        # Set up color scheme
        palette = [
            ('titlebar', 'light green', ''),
            ('hotkey', 'dark green,bold', ''),
            ('quit', 'dark red,bold', ''),
            ('normal', 'white', ''),
            ('selected', 'black', 'light green')]
    
        
        header_text = self.active_powerstrip.cfg.name + u' ' + self.active_powerstrip.cfg['host'] + ':' + self.active_powerstrip.cfg['port']
        if not self.active_powerstrip.get_last_refresh() == None:
            header_test += ' ' + self.active_powerstrip.get_last_refresh()
        self.title = urwid.Text(header_text)
        header = urwid.AttrMap(self.title, 'titlebar')
        self.content = urwid.SimpleListWalker([])
        self.preset1_content = urwid.SimpleListWalker([])
        self.preset1_button = urwid.Button("Activate", on_press = self.activate_preset1)
        self.preset2_content = urwid.SimpleListWalker([])
        self.preset2_button = urwid.Button("Activate", on_press = self.activate_preset2)
        self.preset3_content = urwid.SimpleListWalker([])
        self.preset3_button = urwid.Button("Activate", on_press = self.activate_preset3)
        self.listbox = urwid.ListBox(self.content)
        self.preset1_listbox = urwid.ListBox(self.preset1_content)
        self.preset2_listbox = urwid.ListBox(self.preset2_content)
        self.preset3_listbox = urwid.ListBox(self.preset3_content)
    
        # Create the menu
        menu = urwid.Text([
            u'(', ('hotkey', u'R'), u') reload states  ',
            u'(', ('hotkey', u'E'), u') edit  ',
            u'(', ('hotkey', u'Enter'), u') toggle  ',
            u'(', ('quit', u'Q'), u') quit'
        ])
    
        bodypile = urwid.Pile([self.listbox, urwid.Text(u'Foo')])
        self.outlets_linebox = urwid.LineBox(self.listbox, title="Outlets")
        #self.preset_cols = urwid.Columns([self.preset1_listbox, self.preset2_listbox, self.preset3_listbox])
        #self.presets_linebox = urwid.LineBox(self.preset_cols, title="Presets")
        self.preset1_linebox = urwid.LineBox(self.preset1_listbox, title="Preset 1")
        self.preset2_linebox = urwid.LineBox(self.preset2_listbox, title="Preset 2")
        self.preset3_linebox = urwid.LineBox(self.preset3_listbox, title="Preset 3")
        self.presets_columns = urwid.Columns([self.preset1_linebox, self.preset2_linebox, self.preset3_linebox])
        self.body_pile = urwid.Pile([self.outlets_linebox, self.presets_columns])
         
    
        label_delay = urwid.Text([('hotkey', u'D'), u'elay  '])
        self.layout = urwid.Frame(header=urwid.Columns([header, urwid.Columns([label_delay, urwid.Edit(caption="Multi Power on Delay: ", edit_text=str(self.active_powerstrip.multi_power_on_delay))])]), body=self.body_pile, footer=menu)

        self.top = SignalWrap(self.layout)
        self.top.listen('q', self.quit)
        #self.top.listen('P', self.handle_edit_powerstrip_key)
        #self.top.listen('p', self.handle_edit_powersocket_key)
        self.top.listen('r', self.handle_reload_key)
        self.top.listen('n', self.next_powerstrip)
        self.top.listen('p', self.previous_powerstrip)
        self.top.listen('tab', self.toggle_ui_focus)
 
        self.screen = urwid.raw_display.Screen()
        self.screen.register_palette(palette)



        #self.main_loop = urwid.MainLoop(self.layout, palette, unhandled_input=self.handle_input)
        self.load_outlet_names_and_states()
        if not self.cfg.config_exists():
            print("no config")
            config = self.cfg.init()
        
    def quit(self, w, size, key):
        self.screen.stop()
        self.quit_event_loop = True

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
        self.load_outlet_names_and_states()

    def handle_edit_powerstrip_key(self, w, size, key):
        self.edit_powerstrip(self.selected_powerstrip)
        self.load_outlet_names_and_states()

    def edit_powerstrip_dialog(ui, b):
        edit_name = urwid.Edit("Powerstrip alias: ", b['name'])
        edit_host = urwid.Edit("host: ", b['host'])
        edit_user = urwid.Edit("user: ", b['user'])
        edit_pwd = urwid.Edit("password: ", b['password'])

        lb_contents = ([edit_rating, edit_comment])
        lb = urwid.ListBox(urwid.SimpleListWalker(lb_contents))

        if ui.dialog(lb,         [
                    ("OK", True),
                    ("Cancel", False),
                ],
                title="Edit bookmark"):
            return { 'id': b['id'], "filename": b['filename'], "position": b['position'], "rating": edit_rating.value(), "comment": edit_comment.get_edit_text() }


    # Handle key presses
    def handle_input(self, key):
        if key == 'R' or key == 'r':
           self.load_outlet_names_and_states()
        elif key == 'Q' or key == 'q':
            self.quit()
        elif key == 'tab':
            self.toggle_ui_focus()
        else:
            try:
                outlet_id = int(key)
                if 0 < outlet_id < 9:
                    self.active_powerstrip.toggle_outlet(outlet_id);
                    self.load_outlet_names_and_states()
            except:
                True
                       

    def on_checkbox_toggled(self, arg1, arg2):
        self.toggle_selected_outlet()

    def toggle_selected_outlet(self):
        outlet_id = self.listbox.focus_position + 1
        self.active_powerstrip.toggle_outlet(outlet_id);

    def activate_preset1(self, x):
        self.active_powerstrip.activate_preset(0)
        self.load_outlet_names_and_states()

    def activate_preset2(self, x):
        self.active_powerstrip.activate_preset(1)
        self.load_outlet_names_and_states()

    def activate_preset3(self, x):
        self.active_powerstrip.activate_preset(2)
        self.load_outlet_names_and_states()



    def run(self):
        self.init_ui()
        #self.main_loop.run()
        self.screen.start()
        self.event_loop()

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
                    elif k == 'esc':
                        self.quit_event_loop = [False]
                    else:
                        toplevel.keypress(self.size, k)

            return self.quit_event_loop
        finally:
            self.quit_event_loop = prev_quit_loop


class ConfigManager:
 
    config = None 

    def __init__(self):
        self.config = configparser.ConfigParser()
        self.configfile = expanduser('~/.netpwrctrl.ini')

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
        elif len(argv) > 1:
            command = argv[1]
            outlet_id = int(argv[2])
            ctrl = NetPwrCtrl()
            if command == 'on':
                print("executing command %s" % command)
                try:
                    ctrl.switch_on(outlet_id)
                except:
                    True
            elif command == 'off':
                print("executing command %s" % command)
                try:
                    ctrl.switch_off(outlet_id)
                except:
                    True
            else:
                print("unknown command")
            
    except Usage as err:
        print >>sys.stderr, err.msg
        print >>sys.stderr, "for help use --help"
        return 2

if __name__ == "__main__":
    sys.exit(main())
