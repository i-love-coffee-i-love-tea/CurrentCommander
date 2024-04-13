import urwid
import socket
from wx.lib.mixins.listctrl import ColumnSorterMixin
from subprocess import call
from os.path import expanduser
import httplib2

class NetPwrCtrl:
    def __init__(self):
        # maps outlet names to socket numbers
        self.outlets = {}
        # keeps outlet states by name
        self.states = {}
        self.content = []
        self.host = '192.168.31.121'
        self.cmdPort = 75
        self.user = 'user1'
        self.pwd = 'anel'

    def load_outlet_names_and_states(self):
        self.outlets.clear()
        self.content.clear()
        responseValues = self.fetch_outlet_states()

        # read socket names and create the state map
        with open(expanduser('~/.wxPower.conf'), 'r') as configfile:
            outlet_id = 1
            for line in configfile:
                data = line.strip().split("=")
                self.outlets[data[0]] = data[1]
                self.states[outlet_id] = int(responseValues[6 + (outlet_id * 3)])
                cb = urwid.CheckBox(data[0], self.states[outlet_id], on_state_change=self.on_checkbox_toggled)
                self.content.append(urwid.AttrMap(cb, "normal", "selected"))
                outlet_id += 1

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
        self.listbox = urwid.ListBox(self.content)
    
        # Create the menu
        menu = urwid.Text([
            u'(', ('hotkey', u'R'), u') reload states  ',
            u'(', ('hotkey', u'E'), u') edit  ',
            u'(', ('hotkey', u'Enter'), u') toggle  ',
            u'(', ('quit', u'Q'), u') quit'
        ])
    
        bodypile = urwid.Pile([self.listbox, urwid.Text(u'Foo')])
        self.linebox = urwid.LineBox(self.listbox, title="Outlets")
    
        # Assemble the widgets
        layout = urwid.Frame(header=header, body=self.linebox, footer=menu)
        self.main_loop = urwid.MainLoop(layout, palette, unhandled_input=self.handle_input)
        self.load_outlet_names_and_states()
        

    # Handle key presses
    def handle_input(self, key):
        if key == 'R' or key == 'r':
           self.load_outlet_names_and_states()
        if key == 'Q' or key == 'q':
            raise urwid.ExitMainLoop()

    def fetch_outlet_states(self):
        #h = httplib2.Http(".cache")
        h = httplib2.Http()
        h.add_credentials(self.user, self.pwd)
        (resp_headers, content) = h.request("http://" + self.host + "/?Stat=" + self.user + self.pwd, "GET")
        values = content.decode().split(';')
        return values;

    def on_checkbox_toggled(self, arg1, arg2):
        self.toggle_selected_outlet()

    def toggle_selected_outlet(self):
        outlet_id = self.listbox.focus_position + 1
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


    def run(self):
        self.init_ui()
        self.main_loop.run()



app = NetPwrCtrl()
app.run()
