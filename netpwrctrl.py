import urwid
import socket
from wx.lib.mixins.listctrl import ColumnSorterMixin
from subprocess import call
from os.path import expanduser
import httplib2
import time

class NetPwrCtrl:
    def __init__(self):
        # maps outlet names to socket numbers
        self.outlets = {}
        # keeps outlet states by name
        self.states = {}
        self.content = []
        self.preset1_content = []
        self.preset2_content = []
        self.preset3_content = []
        self.presets = [[0, 0, 0, 0, 0, 0, 0, 0],[0, 0, 0, 0 ,0 ,0 ,0, 0],[0, 0, 0, 0, 0, 0, 0, 0]]
        self.host = '192.168.31.121'
        self.cmdPort = 75
        self.user = 'user1'
        self.pwd = 'anel'
        self.multi_power_on_delay = 2

    def load_outlet_names_and_states(self):
        self.outlets.clear()
        self.content.clear()
        self.preset1_content.clear()
        self.preset2_content.clear()
        self.preset3_content.clear()
        #self.presets[0].clear()
        #self.presets[1].clear()
        #self.presets[2].clear()
        responseValues = self.fetch_outlet_states()

        self.preset1_content.append(urwid.AttrMap(self.preset1_button, "normal", "selected"))
        self.preset1_content.append(urwid.Text(""))
        self.preset2_content.append(urwid.AttrMap(self.preset2_button, "normal", "selected"))
        self.preset2_content.append(urwid.Text(""))
        self.preset3_content.append(urwid.AttrMap(self.preset3_button, "normal", "selected"))
        self.preset3_content.append(urwid.Text(""))
        # read socket names and create the state map
        with open(expanduser('~/.wxPower.conf'), 'r') as configfile:
            outlet_id = 1
            index = 0
            for line in configfile:
                data = line.strip().split("=")
                values = data[1].split(",")
                self.presets[0][outlet_id-1] = int(values[1])
                self.presets[1][outlet_id-1] = int(values[2])
                self.presets[2][outlet_id-1] = int(values[3])
                self.outlets[data[0]] = values[0]
                self.states[outlet_id] = int(responseValues[6 + (outlet_id * 3)])
                cb = urwid.CheckBox(data[0], self.states[outlet_id], on_state_change=self.on_checkbox_toggled)
                self.content.append(urwid.AttrMap(cb, "normal", "selected"))
                cb_preset1 = urwid.CheckBox(data[0], self.presets[0][outlet_id-1])
                self.preset1_content.append(urwid.AttrMap(cb_preset1, "normal", "selected"))
                cb_preset2 = urwid.CheckBox(data[0], self.presets[1][outlet_id-1])
                self.preset2_content.append(urwid.AttrMap(cb_preset2, "normal", "selected"))
                cb_preset3 = urwid.CheckBox(data[0], self.presets[2][outlet_id-1])
                self.preset3_content.append(urwid.AttrMap(cb_preset3, "normal", "selected"))
                outlet_id += 1
                index += 1

    def init_ui(self):
    
        # Set up color scheme
        palette = [
            ('titlebar', 'light green', ''),
            ('hotkey', 'dark green,bold', ''),
            ('quit', 'dark red,bold', ''),
            ('normal', 'white', ''),
            ('selected', 'black', 'light green')]
    
        header_text = urwid.Text(u'NET-PwrCtrl ' + self.host)
        header = urwid.AttrMap(header_text, 'titlebar')
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
         
    
        # Assemble the widgets
        self.layout = urwid.Frame(header=urwid.Columns([header, urwid.Edit(caption="Multi Power on Delay: ", edit_text=str(self.multi_power_on_delay))]), body=self.body_pile, footer=menu)
        self.main_loop = urwid.MainLoop(self.layout, palette, unhandled_input=self.handle_input)
        self.load_outlet_names_and_states()
        

    def activate_preset1(self, x):
        self.activate_preset(0)
    def activate_preset2(self, x):
        self.activate_preset(1)
    def activate_preset3(self, x):
        self.activate_preset(2)

    def activate_preset(self, preset_index):
        outlet_id = 1
        outlets_switched_on = 0
        for p in self.presets[preset_index]:
            if p != self.states[outlet_id]:
                # sleep before toggling the next outlet
                if outlets_switched_on > 0:
                    time.sleep(self.multi_power_on_delay)
                self.toggle_outlet(outlet_id)
                if p == 1:
                   outlets_switched_on += 1
            outlet_id += 1
        self.load_outlet_names_and_states()

    # Handle key presses
    def handle_input(self, key):
        if key == 'R' or key == 'r':
           self.load_outlet_names_and_states()
        if key == 'Q' or key == 'q':
            raise urwid.ExitMainLoop()
        if key == 'tab':
           if self.layout.get_focus() == 'body':
               self.layout.focus_position = 'header'
           elif self.layout.get_focus() == 'header':
               self.layout.focus_position = 'body'

    def fetch_outlet_states(self):
        #h = httplib2.Http(".cache")
        h = httplib2.Http()
        h.add_credentials(self.user, self.pwd)
        (resp_headers, content) = h.request("http://" + self.host + "/?Stat=" + self.user + self.pwd, "GET")
        values = content.decode().split(';')
        return values;

    def on_checkbox_toggled(self, arg1, arg2):
        self.toggle_selected_outlet()

    def toggle_outlet(self, outlet_id):
        current_state = self.states[outlet_id]
        command = "Sw_on"
        focus_widget, idx = self.listbox.get_focus()
        if current_state == 1:
            command = "Sw_off"
            self.states[outlet_id] = 0
        else:
            self.states[outlet_id] = 1
        s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        s.sendto((command + str(outlet_id) + self.user + self.pwd +"\n").encode(), (self.host, self.cmdPort))

    def toggle_selected_outlet(self):
        outlet_id = self.listbox.focus_position + 1
        self.toggle_outlet(outlet_id);


    def run(self):
        self.init_ui()
        self.main_loop.run()





app = NetPwrCtrl()
app.run()
    
